import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import collect  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def test_radnet_list():
    rows = collect.parse_radnet_list((FIX / "radnet_list.html").read_text(encoding="utf-8"))
    assert len(rows) == 28
    assert rows[0]["art"] == "vRTF"
    assert rows[0]["date"] == "2026-10-15"
    assert rows[0]["url"].startswith("https://breitensport.rad-net.de/breitensportkalender/termine/")


def test_radnet_detail():
    d = collect.parse_radnet_detail((FIX / "radnet_detail.html").read_text(encoding="utf-8"))
    assert d["plz"] == "31303"
    assert d["place"] == "Burgdorf"
    assert d["website"] == "http://www.rsb-hannover.de"
    assert [r["km"] for r in d["routes"]] == [44, 66]
    assert d["routes"][0]["elevation_m"] is None


def test_dlv():
    data = json.loads((FIX / "dlv_search.json").read_text())
    rows = collect.parse_dlv_html(data["events"])
    dresden = next(r for r in rows if "DRESDEN" in r["name"])
    assert dresden["date"] == "2026-10-24"
    assert dresden["plz"] == "01067" and dresden["place"] == "Dresden"
    assert dresden["kms"] == [5, 42.195]
    cats = collect.dlv_categories(dresden["codes"], dresden["kms"])
    assert {"Marathon", "Halbmarathon", "10 km", "5 km"} <= set(cats)


def test_dlv_categories_fallback():
    assert collect.dlv_categories([], [3.0]) == ["Sonstige"]
    assert collect.dlv_categories([], [10, 50]) == ["10 km", "Ultra"]


def test_rse_event():
    x = json.loads((FIX / "rse_event.json").read_text())
    e = collect.rse_event(x)
    assert e["name"] == "Ötztaler Radmarathon"
    assert e["date"] == "2027-08-29"
    assert e["country"] == "AT"
    assert e["categories"] == ["Radmarathon"]
    assert e["routes"][0]["km"] == 227 and e["routes"][0]["elevation_m"] == 5500
    assert e["website"].startswith("https://")
    assert e["registration_status"] == "Anmeldung noch nicht geöffnet"


def test_rse_skips_mtb():
    x = json.loads((FIX / "rse_event.json").read_text())
    x.update(category="MTB", eventType="MTB_MARATHON")
    assert collect.rse_event(x) is None


def test_dedupe_prefers_curated():
    cur = {"sport": "rennrad", "date": "2027-08-29", "name": "Ötztaler Radmarathon", "source": "kuratiert", "lat": 46.9655, "lon": 11.0076}
    rse = {"sport": "rennrad", "date": "2027-08-29", "name": "Ötztaler Radmarathon 2027", "source": "radsport-events.de", "lat": 46.965, "lon": 11.007}
    other = {"sport": "rennrad", "date": "2027-08-29", "name": "RTF Hamburg", "source": "rad-net", "lat": 53.55, "lon": 9.99}
    out = collect.dedupe([rse, other, cur])
    assert sorted(e["source"] for e in out) == ["kuratiert", "rad-net"]
