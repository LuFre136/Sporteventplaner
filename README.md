# Sporteventplaner

Kostenlose Web-App fürs iPhone, mit der du Rennrad- und Laufevents findest und Event oder Anmeldestart als `.ics` in den Kalender übernimmst.

## Woher die Daten kommen

| Quelle | Sportart | Region | Inhalt |
|---|---|---|---|
| [rad-net Breitensportkalender](https://breitensport.rad-net.de/breitensportkalender/) | Rennrad (RTF, Radmarathon, Brevet, Gravel, Etappenfahrt) | Deutschland | Datum, Strecken, Höhenmeter (falls angegeben), Startort, Webseite |
| [DLV-Laufkalender](https://www.laufen.de/laufkalender) | Laufen (5 km, 10 km, Halbmarathon, Marathon, Ultra) | v. a. Deutschland | Datum, Ort, kürzeste/längste Strecke, Webseite |
| [`data/curated.json`](data/curated.json) | beides | weltweit | von Hand gepflegt, mit Anmeldezeitraum, Gebühr, Höhenmetern |

`scripts/collect.py` liest die Quellen jeden Montag über GitHub Actions ([`.github/workflows/collect.yml`](.github/workflows/collect.yml)) neu ein, geokodiert die Orte über OpenStreetMap Nominatim (Cache in `data/geocache.json`) und schreibt alles nach `docs/events.json`.

## Ein Event von Hand hinzufügen

Trage es in `data/curated.json` ein. Nach dem Commit läuft der Workflow automatisch und übernimmt es. Pflichtfelder sind `sport` (`rennrad` oder `laufen`), `categories`, `name`, `date` und `country` (ISO-Code, z. B. `IT`). Optional sind `end_date`, `place`, `lat`/`lon`, `website`, `routes` (`[{"name", "km", "elevation_m"}]`), `registration_start`, `registration_end`, `fee` und `notes`.

## Lokal ausführen

```bash
pip install -r requirements.txt pytest
python -m pytest tests
python scripts/collect.py --dlv-max-pages 2   # kurzer Testlauf
```
