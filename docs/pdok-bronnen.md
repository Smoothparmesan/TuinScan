# PDOK-bronnen — geverifieerd 2026-11-25

Getest vanuit de cloud-ontwikkelomgeving op een proefgebied in Amsterdam (RD ≈ 120 000–120 600, 485 300–485 800). Proxy van de werklaptop nog niet getest (Task 6).

## Vector (OGC API Features)

| Bron | Basis-URL | Collectie | Velden die we gebruiken |
| --- | --- | --- | --- |
| BAG | `https://api.pdok.nl/kadaster/bag/ogc/v2` | `verblijfsobject` | `identificatie` (16 cijfers, typecode `01`), `gebruiksdoel` (`woonfunctie`), `pand.href` (lijst URL's; laatste segment = feature-`id` van het pand), `openbare_ruimte_naam`, `huisnummer`, `huisletter`, `toevoeging`, `postcode`, `woonplaats_naam` |
| BAG | idem | `pand` | feature-`id` (UUID), `identificatie`, `aantal_verblijfsobjecten`, `gebruiksdoel` |
| BRK | `https://api.pdok.nl/kadaster/brk-kadastrale-kaart/ogc/v1` | `perceel` | `identificatie_lokaal_id` (MultiPolygon) |
| BGT | `https://api.pdok.nl/lv/bgt/ogc/v1` | `wegdeel` | `functie`: o.a. `voetpad`, `parkeervlak`, `rijbaan lokale weg`, `inrit` |
| BGT | idem | `begroeidterreindeel` | `fysiek_voorkomen`: o.a. `groenvoorziening`, `loofbos`, `gemengd bos` |

- `bbox`, `bbox-crs` en `crs` = `http://www.opengis.net/def/crs/EPSG/0/28992` werken; antwoord heeft header `Content-Crs` RD.
- `limit=1000` levert 1000 features per pagina; paging via `links[rel=next]`.
- **Let op:** BAG-verblijfsobject heeft geen veld `pandidentificatie`. Koppeling vbo → pand via `pand.href` ↔ feature-`id` van het pand (fallback: punt-in-polygoon).

## Luchtfoto's (WMTS)

- RGB: `https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0`, CIR: `https://service.pdok.nl/hwh/luchtfotocir/wmts/v1_0`.
- REST-template: `{basis}/{laag}/EPSG:28992/{zoom}/{col}/{row}.jpeg`. Matrixset `EPSG:28992`, origin (-285401.92, 903401.92), 256 px; zoom 14 = 0,21 m, 15 = 0,105 m, 16 = 0,0525 m.
- **Gemeten (ExG/NDVI op boomkruinen):**
  - `YYYY_ortho25` (RGB 25 cm) = **zomervlucht, met blad** (kruin-ExG 0,13).
  - `YYYY_ortho25IR` (CIR 25 cm) = **zomervlucht, met blad**, zelfde schaduwen als `ortho25` (kruin-NDVI 0,33 in 2025, 0,27 in 2024).
  - `YYYY_orthoHR` (RGB 8/5 cm) en `YYYY_orthoHRIR` = **wintervlucht, zonder blad** (kruin-ExG −0,03, NDVI 0,02).
- Dit wijkt af van de spec ("een infrarood-zomeropname bestaat niet"): PDOK levert wél zomer-infrarood op 25 cm.
- CIR-banden: band 1 = NIR, band 2 = rood, band 3 = groen. NDVI = (b1 − b2)/(b1 + b2). JPEG-stretch drukt de NDVI: startdrempel 0,2 i.p.v. 0,3.
- Beschikbaar nu: zomer `2025_ortho25`, `2024_ortho25` (+ IR); winter `2026_orthoHR`, `2025_orthoHR`. `2026_quickortho25` gaf op het proefgebied een volledig witte tegel (HTTP 200): nog geen dekking. Witte of zwarte tegels dus als ontbrekend behandelen.

### Kalibratie begroeiingsmasker (Houten, RD 139850–140150 / 447550–447850, 2026-11-25)

Gemeten met `tuinscan kalibreer` op zonbeschenen pixels, klassen 1 m naar binnen verkleind (voetpad 0,3 m):

| Laag | rijbaan p50 | dak p50 / p95 | BGT-groen p25 / p50 | voorstel |
| --- | --- | --- | --- | --- |
| `2025_ortho25IR` (NDVI) | −0,05 | 0,10 / 0,28 | 0,28 / 0,42 | 0,26 |
| `2024_ortho25IR` (NDVI) | −0,11 | −0,10 / 0,18 | 0,23 / 0,27 | 0,18 |
| `2025_ortho25` (ExG) | 0,04 | 0,07 / 0,13 | 0,13 / 0,17 | 0,12 |
| `2024_ortho25` (ExG) | 0,05 | 0,09 / 0,13 | 0,13 / 0,14 | 0,12 |

- De NDVI-schaal verschilt per vlucht (groen 2025 p50 0,42 tegen 0,27 in 2024): één vaste drempel vergelijkt de jaren niet eerlijk. Daarom kan elke zomerlaag een eigen drempel hebben (`luchtfoto.lagen.<laag>.ndvi_drempel`/`exg_drempel`).
- ExG scheidt slecht (dak p95 ≈ groen p25); het masker gebruikt daarom NDVI waar CIR geldig is en ExG alleen als terugval (startdrempel 0,12; de oude 0,05 zag 34–61 % van de rijbaan en 77–92 % van de daken als groen).
- Met de voorstellen: rijbaan 4 % / 2 %, daken 8 % / 4 %, BGT-groen 79 % / 85 % groen (2025 / 2024). In 2025 overlappen dak en groen: ~85 % groen is daar niet haalbaar zonder meer daken als groen te zien. Voetpaden blijven 30–40 % "groen" (overhangend groen, scheve daken en bomen).
- De zomerfoto's zijn geen true-ortho: daken hellen ~0,5–2 m (Houten) tot 3,5–5 m (Amsterdam) over de tuin; winter-HR ~0,25–1 m.

## AHN (COG)

- Kaartbladindex DSM: `https://service.pdok.nl/rws/actueel-hoogtebestand-nederland/atom/downloads/dsm_05m/kaartbladindex.json`
- Kaartbladindex DTM: `https://service.pdok.nl/rws/actueel-hoogtebestand-nederland/atom/downloads/dtm_05m/kaartbladindex.json`
- GeoJSON in EPSG:28992, kaartbladen 5 × 6,25 km; COG-URL in property `url` (DSM `R_*.tif`, DTM `M_*.tif`).
- `/vsicurl/` + windowed read werkt: venster van 100 × 100 m in ~3–4 s. float32, 0,5 m, nodata = float32-max.
- DTM heeft grote gaten onder gebouwen (58 % nodata in een stedelijk venster) → gaten vullen met een ruime zoekafstand.
- De feed bevat alleen "actueel AHN", geen vorige versie → S4a "nieuw t.o.v. vorige AHN" kan in de PoC niet uit PDOK.
