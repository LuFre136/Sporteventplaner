import sys, re, collections, datetime as dt
sys.path.insert(0, "scripts")
import collect as c
from bs4 import BeautifulSoup
start = dt.date.today(); end = start + dt.timedelta(days=450)
for ls in (0, 25, 50):
    url = c.radnet_list_url(start, end, ls)
    html = c.get(url).text
    rows = c.parse_radnet_list(html)
    print("lstart", ls, "rows", len(rows), collections.Counter(r["art"] for r in rows).most_common(12))
    print(" dates", rows[0]["date"] if rows else None, rows[-1]["date"] if rows else None)
    if ls == 0:
        soup = BeautifulSoup(html, "lxml")
        print(" lstart links", sorted(set(re.findall(r"lstart=(\d+)", html)), key=int)[:30])
        for t in soup.find_all(string=re.compile(r"Treffer|Termine|Ergebnis|Seite", re.I))[:10]:
            print(" TXT", t.strip()[:150])
        for a in soup.select("a[href*=lstart]")[:10]:
            print(" A", a.get("href")[:200], a.get_text(" ", strip=True))
        for sel in ("select[name=art]", "select[name=lv]"):
            s = soup.select_one(sel)
            if s: print(sel, [(o.get("value"), o.get_text(strip=True)) for o in s.find_all("option")][:40])
