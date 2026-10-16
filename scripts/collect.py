#!/usr/bin/env python3
"""Sammelt Rennrad- und Laufevents und schreibt docs/events.json.

Quellen:
  * rad-net Breitensportkalender (Rennrad, Deutschland)
  * DLV-Laufkalender auf laufen.de (Laufen, vor allem Deutschland)
  * data/curated.json (handgepflegte Events, z. B. international)

Orte werden über OpenStreetMap Nominatim geokodiert und in data/geocache.json
zwischengespeichert, damit wöchentliche Läufe nur neue Orte abfragen.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "docs" / "events.json"
GEOCACHE = DATA / "geocache.json"
GPX_DIR = ROOT / "docs" / "gpx"
CURATED = DATA / "curated.json"

UA = "Sporteventplaner/1.0 (+https://github.com/LuFre136/sporteventplaner)"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = UA

# ---------------------------------------------------------------- helpers


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def get(url, **kw):
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=40, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            log(f"  retry {attempt + 1} {url}: {e}")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"failed: {url}")


def parse_de_date(s: str) -> str | None:
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s or "")
    if not m:
        return None
    d, mo, y = map(int, m.groups())
    return dt.date(y, mo, d).isoformat()


def num(s: str) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", s or "")
    return float(m.group(0).replace(",", ".")) if m else None


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def make_id(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]


def site(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    return url if url.startswith("http") else "https://" + url


# ---------------------------------------------------------------- rad-net

RADNET = "https://breitensport.rad-net.de"
# rad-net "Art" -> Kategorie in der App. CTF (MTB), Permanente und vRTF fehlen bewusst.
RADNET_CATEGORIES = {
    "RTF": "RTF",
    "Radmarathon": "Radmarathon",
    "RMCD": "Radmarathon",
    "Brevet": "Brevet",
    "Gravelride": "Gravel",
    "Gravelride-Marathon": "Gravel",
    "Gravel-Marathon": "Gravel",
    "Etappenfahrt": "Etappenfahrt",
    "Etappenfahrten": "Etappenfahrt",
}


def radnet_list_url(start: dt.date, end: dt.date, lstart: int = 0) -> str:
    return (
        f"{RADNET}/breitensportkalender/?titel=&startdate={start:%d.%m.%Y}"
        f"&enddate={end:%d.%m.%Y}&art=-1&lv=-1&umkreis=-1&plz=&updatet="
        f"&sortbyn=date&lstart={lstart}"
    )


def parse_radnet_list(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for a in soup.select("li.tabzeile a.terminlink"):
        cells = a.select(":scope > div.zelle")
        if len(cells) < 5:
            continue
        rows.append(
            {
                "art": clean(cells[0].get_text(" ")),
                "date": parse_de_date(cells[1].get_text()),
                "name": clean(cells[2].get_text(" ")),
                "url": urljoin(RADNET, a["href"]),
            }
        )
    return rows


def parse_radnet_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    box = soup.find(id="termindetails")
    fields: dict[str, object] = {}
    if box:
        for tr in box.find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if th and td:
                fields[clean(th.get_text())] = td
    out: dict = {}
    if "Startort" in fields:
        lines = [clean(x) for x in fields["Startort"].stripped_strings]
        lines = [l for l in lines if l and l != "Route erstellen"]
        out["address"] = ", ".join(lines)
        plz = next((l for l in lines if re.match(r"^\d{5}\b", l)), None)
        out["place"] = clean(re.sub(r"^\d{5}", "", plz)) if plz else (lines[1] if len(lines) > 1 else "")
        out["plz"] = plz[:5] if plz else None
    if "Internet" in fields:
        a = fields["Internet"].find("a")
        out["website"] = site(a["href"] if a else fields["Internet"].get_text())
    if "Zusatz" in fields:
        out["zusatz"] = clean(fields["Zusatz"].get_text(" "))
    if "Startzeit" in fields:
        out["start_time"] = clean(fields["Startzeit"].get_text())
    if "Veranstalter" in fields:
        out["organizer"] = clean(fields["Veranstalter"].get_text())
    # Streckentabelle: "44 km:" | Profil | HM
    routes = []
    h2 = soup.find(lambda t: t.name == "h2" and "Streckeninformationen" in t.get_text())
    table = h2.find_parent("table") if h2 else None
    if table:
        for tr in table.find_all("tr"):
            th, tds = tr.find("th"), tr.find_all("td")
            if not th or len(tds) < 2:
                continue
            km = num(th.get_text())
            hm = num(tds[1].get_text())
            if km:
                routes.append({"km": km, "elevation_m": int(hm) if hm else None, "profile": clean(tds[0].get_text()) or None})
    if not routes and "Strecken" in fields:
        routes = [{"km": float(k), "elevation_m": None} for k in re.findall(r"\d+", fields["Strecken"].get_text())]
    out["routes"] = routes
    return out


def collect_radnet(months: int, delay: float) -> list[dict]:
    start = dt.date.today()
    end = start + dt.timedelta(days=30 * months)
    rows = parse_radnet_list(get(radnet_list_url(start, end)).text)
    log(f"rad-net: {len(rows)} Termine gelistet")
    events = []
    for row in rows:
        cat = RADNET_CATEGORIES.get(row["art"])
        if not cat or not row["date"]:
            continue
        try:
            det = parse_radnet_detail(get(row["url"]).text)
        except RuntimeError as e:
            log(f"  {e}")
            continue
        time.sleep(delay)
        events.append(
            {
                "id": "rn-" + make_id(row["url"]),
                "sport": "rennrad",
                "categories": [cat],
                "name": row["name"],
                "date": row["date"],
                "country": "DE",
                "place": det.get("place"),
                "address": det.get("address"),
                "geo_query": det.get("address"),
                "website": det.get("website"),
                "source": "rad-net",
                "source_url": row["url"],
                "routes": det.get("routes", []),
                "registration_start": None,
                "registration_end": None,
                "fee": None,
                "notes": ("Voranmeldung erforderlich" if re.search(r"\bV\b", det.get("zusatz") or "") else None),
            }
        )
    log(f"rad-net: {len(events)} Rennrad-Events übernommen")
    return events


# ---------------------------------------------------------------- DLV

DLV = "https://www.laufen.de/"
DLV_AJAX = "https://www.laufen.de/laufkalender/ajax/search"


def dlv_categories(codes: list[str], kms: list[float]) -> list[str]:
    cats = set()
    for c in codes:
        c = c.replace("\xa0", " ").strip().upper()
        if c == "M":
            cats.add("Marathon")
        elif c == "HM":
            cats.add("Halbmarathon")
        elif c == "10 KM":
            cats.add("10 km")
        elif c == "5 KM":
            cats.add("5 km")
    for k in kms:
        if abs(k - 42.195) < 0.3:
            cats.add("Marathon")
        elif abs(k - 21.0975) < 0.2:
            cats.add("Halbmarathon")
        elif abs(k - 10) < 0.01:
            cats.add("10 km")
        elif abs(k - 5) < 0.01:
            cats.add("5 km")
        elif k > 42.3:
            cats.add("Ultra")
    return sorted(cats) or ["Sonstige"]


def parse_dlv_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select("a.teaser.event"):
        date_el = a.select_one(".date")
        name_el = a.select_one(".headline")
        loc_el = a.select_one(".location")
        if not (date_el and name_el):
            continue
        loc = clean(loc_el.get_text(" ")) if loc_el else ""
        m = re.match(r"^(\d{4,5})\s+(.*)$", loc)
        plz, place = (m.group(1), m.group(2)) if m else (None, loc)
        rng = clean(a.select_one(".wettbewerbe").get_text(" ")) if a.select_one(".wettbewerbe") else ""
        kms = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:,\d+)?", rng)]
        codes = [clean(c.get_text(" ")) for c in a.select(".codes .code")]
        out.append(
            {
                "href": urljoin(DLV, a["href"]),
                "date": parse_de_date(date_el.get_text()),
                "name": clean(name_el.get_text(" ")),
                "plz": plz,
                "place": place,
                "kms": kms,
                "codes": codes,
            }
        )
    return out


def dlv_page(page: int) -> dict:
    data = dict(search="", radius="0", start="", end="", distance_start="0", distance_end="150", distances="[]", page=str(page))
    for attempt in range(3):
        try:
            r = SESSION.post(DLV_AJAX, data=data, headers={"X-Requested-With": "XMLHttpRequest"}, timeout=40)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            log(f"  retry {attempt + 1} DLV page {page}: {e}")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"DLV page {page} failed")


def resolve_redirect(url: str) -> str:
    try:
        r = SESSION.head(url, allow_redirects=False, timeout=20)
        loc = r.headers.get("Location")
        if r.status_code in (301, 302, 303, 307, 308) and loc and "laufen.de" not in loc:
            return urljoin(url, loc)
    except requests.RequestException:
        pass
    return url


def collect_dlv(delay: float, resolve_sites: bool, max_pages: int | None) -> list[dict]:
    first = dlv_page(1)
    pages = int(first.get("pages") or 1)
    if max_pages:
        pages = min(pages, max_pages)
    log(f"DLV: {first.get('total')} Events auf {pages} Seiten")
    seen: dict[str, dict] = {}
    for p in range(1, pages + 1):
        d = first if p == 1 else dlv_page(p)
        for row in parse_dlv_html((d.get("topevents") or "") + (d.get("events") or "")):
            seen.setdefault(row["href"], row)
        time.sleep(delay)
    events = []
    for row in seen.values():
        if not row["date"]:
            continue
        website = resolve_redirect(row["href"]) if resolve_sites else row["href"]
        if resolve_sites:
            time.sleep(delay / 2)
        kms = sorted(set(row["kms"]))
        events.append(
            {
                "id": "dlv-" + make_id(row["href"]),
                "sport": "laufen",
                "categories": dlv_categories(row["codes"], kms),
                "name": row["name"],
                "date": row["date"],
                # Fünfstellige PLZ = Deutschland; sonst kommt das Land aus der Geokodierung
                "country": "DE" if row["plz"] and len(row["plz"]) == 5 else None,
                "place": row["place"],
                "address": " ".join(x for x in (row["plz"], row["place"]) if x),
                "geo_query": " ".join(x for x in (row["plz"], row["place"]) if x),
                "website": website if website != row["href"] else None,
                "source": "DLV-Laufkalender",
                "source_url": row["href"],
                # Laufen.de nennt nur die kürzeste und längste Strecke
                "routes": [{"km": k, "elevation_m": None} for k in kms],
                "distance_range_only": len(kms) == 2,
                "registration_start": None,
                "registration_end": None,
                "fee": None,
                "notes": None,
            }
        )
    log(f"DLV: {len(events)} Lauf-Events übernommen")
    return events



# ---------------------------------------------------------------- radsport-events.de

RSE_API = "https://api.radsport-events.de/api/v1"
# eventType -> Kategorie in der App; nicht aufgeführte Typen (MTB, Cross, Triathlon …) fehlen bewusst
RSE_TYPES = {
    "RTF": "RTF",
    "RADMARATHON": "Radmarathon",
    "JEDERMANN": "Rennen",
    "RENNEN_24H": "Rennen",
    "ETAPPENRENNEN": "Etappenfahrt",
    "ULTRA": "Ultra",
    "BIKEPACKING": "Ultra",
    "BREVET": "Brevet",
    "VOLKSRADFAHREN": "Radtour",
    "GRAVEL_RACE": "Gravel",
    "GRAVEL_RIDE": "Gravel",
    "GRAVEL_TOUR": "Gravel",
}
RSE_REG_STATUS = {
    "OPEN": "Anmeldung offen",
    "NOT_YET_OPEN": "Anmeldung noch nicht geöffnet",
    "CLOSED": "Anmeldung geschlossen",
    "ONSITE_ONLY": "Nur Nachmeldung vor Ort",
}


def is_url(s: str | None) -> bool:
    return bool(s and re.match(r"^https?://\S+$", s.strip()))


def rse_route(event_id: int, d: dict) -> dict:
    route = {"name": d.get("label"), "km": d.get("distanceKm"), "elevation_m": d.get("elevationMeters") or None}
    if d.get("hasGpx") and d.get("id"):
        route["gpx"] = f"{RSE_API}/events/{event_id}/distances/{d['id']}/gpx"
    # routeUrl ist ein Freitextfeld: nur echte Links (komoot, Strava …) übernehmen
    if is_url(d.get("routeUrl")):
        route["route_url"] = d["routeUrl"].strip()
    return route


def rse_event(x: dict) -> dict | None:
    cats = set(x.get("categories") or [x.get("category")]) | set(x.get("secondaryCategories") or [])
    if "ROAD" not in cats and "GRAVEL" not in cats:
        return None
    cat = RSE_TYPES.get(x.get("eventType") or "")
    if not cat or x.get("cancelled") or x.get("permanent") or not x.get("eventDate"):
        return None
    notes = []
    if x.get("tentative"):
        notes.append("Termin noch vorläufig.")
    if x.get("fullyBooked"):
        notes.append("Ausgebucht.")
    return {
        "id": f"rse-{x['id']}",
        "sport": "rennrad",
        "categories": [cat],
        "name": clean(x.get("title")),
        "date": x["eventDate"],
        "end_date": x.get("endDate") if x.get("endDate") and x.get("endDate") != x["eventDate"] else None,
        "country": x.get("country"),
        "place": x.get("city") or x.get("locationName"),
        "address": x.get("address"),
        "lat": x.get("latitude"),
        "lon": x.get("longitude"),
        "geo_query": x.get("address"),
        "website": site(x.get("organizerUrl")),
        "source": "radsport-events.de",
        "source_url": f"https://radsport-events.de/events/{x['id']}",
        "routes": [rse_route(x["id"], d) for d in sorted(x.get("distances") or [], key=lambda d: d.get("distanceKm") or 0) if d.get("distanceKm")],
        "komoot": x.get("komootCollectionUrl") if is_url(x.get("komootCollectionUrl")) else None,
        "registration_status": RSE_REG_STATUS.get(x.get("registrationStatus") or ""),
        "notes": " ".join(notes) or None,
    }


def collect_rse(delay: float) -> list[dict]:
    events, page, pages = [], 0, 1
    while page < pages:
        r = get(f"{RSE_API}/events", params={"page": page, "size": 100}, headers={"Accept": "application/json"})
        data = r.json()
        pages = int(data.get("totalPages") or 1)
        for x in data.get("content") or []:
            e = rse_event(x)
            if e:
                events.append(e)
        page += 1
        time.sleep(delay)
    log(f"radsport-events.de: {len(events)} Rennrad-/Gravel-Events übernommen")
    return events

def mirror_gpx(events: list[dict], delay: float) -> None:
    """GPX-Dateien von radsport-events.de nach docs/gpx kopieren.

    Die API erlaubt keine Abrufe aus fremden Webseiten (CORS), deshalb liefert
    GitHub Pages die Dateien selbst aus. Vorhandene Dateien werden nicht neu
    geladen, nicht mehr verwendete gelöscht.
    """
    GPX_DIR.mkdir(parents=True, exist_ok=True)
    used, loaded = set(), 0
    for e in events:
        for r in e.get("routes") or []:
            m = re.match(re.escape(RSE_API) + r"/events/\d+/distances/(\d+)/gpx$", r.get("gpx") or "")
            if not m:
                continue
            name = f"rse-{m.group(1)}.gpx"
            path = GPX_DIR / name
            if not path.exists():
                try:
                    body = get(r["gpx"]).content
                except RuntimeError as err:
                    log(f"  {err}")
                    body = b""
                time.sleep(delay)
                if b"<gpx" not in body[:2000]:
                    del r["gpx"]
                    continue
                path.write_bytes(body)
                loaded += 1
            r["gpx"] = f"gpx/{name}"
            used.add(name)
    for f in GPX_DIR.glob("rse-*.gpx"):
        if f.name not in used:
            f.unlink()
    log(f"GPX: {len(used)} Dateien, {loaded} neu geladen")


# ---------------------------------------------------------------- geocoding


class Geocoder:
    def __init__(self, path: Path, budget: int):
        self.path = path
        self.cache = json.loads(path.read_text()) if path.exists() else {}
        self.budget = budget
        self.last = 0.0

    def save(self):
        self.path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=0, sort_keys=True))

    def lookup(self, query: str | None, country_hint: str | None) -> dict | None:
        if not query:
            return None
        key = f"{query}|{country_hint or ''}".lower()
        if key in self.cache:
            return self.cache[key]
        if self.budget <= 0:
            return None
        self.budget -= 1
        # Nominatim-Nutzungsrichtlinie: max. 1 Anfrage pro Sekunde
        wait = 1.1 - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        params = {"q": query, "format": "jsonv2", "limit": 1, "addressdetails": 1}
        if country_hint:
            params["countrycodes"] = country_hint.lower()
        res = None
        try:
            r = SESSION.get("https://nominatim.openstreetmap.org/search", params=params, timeout=30)
            r.raise_for_status()
            hits = r.json()
            if not hits and "," in query:  # Straße weglassen und mit Ort erneut versuchen
                return self.lookup(query.split(",", 1)[1].strip(), country_hint)
            if not hits and re.match(r"^\d{5} ", query):  # Ortsteil unbekannt: nur PLZ
                return self.lookup(query[:5], country_hint)
            if hits:
                h = hits[0]
                res = {
                    "lat": round(float(h["lat"]), 5),
                    "lon": round(float(h["lon"]), 5),
                    "country": (h.get("address", {}).get("country_code") or "").upper() or None,
                }
        except (requests.RequestException, ValueError) as e:
            log(f"  geocode failed {query}: {e}")
            return None
        self.cache[key] = res
        return res


# ---------------------------------------------------------------- main


def load_curated() -> list[dict]:
    if not CURATED.exists():
        return []
    items = json.loads(CURATED.read_text())["events"]
    for e in items:
        e.setdefault("id", "cur-" + make_id(e["name"], e["date"]))
        e.setdefault("source", "kuratiert")
        e.setdefault("geo_query", e.get("address") or e.get("place"))
    return items


SOURCE_PRIORITY = {"kuratiert": 0, "radsport-events.de": 1, "rad-net": 2, "DLV-Laufkalender": 3}


def _km(a: dict, b: dict) -> float:
    import math

    if None in (a.get("lat"), a.get("lon"), b.get("lat"), b.get("lon")):
        return math.inf
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def _name_key(name: str) -> str:
    return re.sub(r"\W+", "", re.sub(r"^\d+\.\s*", "", name.lower()))[:20]


def dedupe(events: list[dict]) -> list[dict]:
    """Dasselbe Event aus mehreren Quellen nur einmal behalten (Vorrang: kuratiert, radsport-events, rad-net).

    Gleich gilt: gleiche Sportart, gleiches Startdatum und entweder ähnlicher Name
    oder Startorte höchstens 15 km voneinander entfernt.
    """
    kept: list[dict] = []
    by_day: dict[tuple, list[dict]] = {}
    for e in sorted(events, key=lambda x: SOURCE_PRIORITY.get(x.get("source"), 9)):
        same_day = by_day.setdefault((e["sport"], e["date"]), [])
        dup = any(
            k.get("source") != e.get("source")
            and (_name_key(k["name"]) == _name_key(e["name"]) or _km(k, e) <= 15)
            for k in same_day
        )
        if not dup:
            same_day.append(e)
            kept.append(e)
    return sorted(kept, key=lambda x: (x["date"], x["name"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=15, help="Zeitraum ab heute")
    ap.add_argument("--delay", type=float, default=0.5, help="Pause zwischen Anfragen")
    ap.add_argument("--geocode-budget", type=int, default=3000)
    ap.add_argument("--dlv-max-pages", type=int, default=None)
    ap.add_argument("--no-resolve", action="store_true", help="DLV-Webseiten nicht auflösen")
    ap.add_argument("--skip", action="append", default=[], choices=["radnet", "dlv", "rse"])
    args = ap.parse_args()

    previous = json.loads(OUT.read_text())["events"] if OUT.exists() else []
    events: list[dict] = []
    for name, fn in (
        ("radnet", lambda: collect_radnet(args.months, args.delay)),
        ("dlv", lambda: collect_dlv(args.delay, not args.no_resolve, args.dlv_max_pages)),
        ("rse", lambda: collect_rse(args.delay)),
    ):
        src = {"radnet": "rad-net", "dlv": "DLV-Laufkalender", "rse": "radsport-events.de"}[name]
        keep = [e for e in previous if e.get("source") == src]
        if name in args.skip:
            events += keep
            continue
        try:
            events += fn()
        except Exception as e:  # Eine kaputte Quelle soll die andere nicht verhindern
            log(f"{name} fehlgeschlagen: {e!r}; behalte vorherige Daten")
            events += keep

    curated = load_curated()
    events += curated

    geo = Geocoder(GEOCACHE, args.geocode_budget)
    for e in events:
        if e.get("lat") is not None and e.get("lon") is not None:
            continue
        g = geo.lookup(e.get("geo_query"), e.get("country"))
        if g:
            e["lat"], e["lon"] = g["lat"], g["lon"]
            e["country"] = e.get("country") or g["country"]
    geo.save()

    today = dt.date.today().isoformat()
    final = []
    for e in events:
        if (e.get("end_date") or e["date"]) < today:
            continue
        e.pop("geo_query", None)
        final.append({k: v for k, v in e.items() if v not in (None, [], "")})
    final = dedupe(final)
    mirror_gpx(final, args.delay)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "count": len(final), "events": final},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    log(f"{len(final)} Events nach {OUT.relative_to(ROOT)} geschrieben")


if __name__ == "__main__":
    main()
