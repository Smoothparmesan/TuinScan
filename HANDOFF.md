# HANDOFF — Tuinscan

## Status (2026-11-25)
- Alle code-taken (1-15) klaar; `uv run pytest` (237) en `uv run ruff check .` groen. Plan: `docs/superpowers/plans/2026-11-25-tuinscan-poc.md`.
- Commando's: `inspecteer`, `download`, `voorbeeld`, `tuinen`, `score`, `rapport`, `terugkoppeling maak|lees`, `alles`, `opruimen`. Zie README.md.

## Draaien
`uv sync` → config kopiëren uit `config/tuinscan.example.yaml` (data buiten OneDrive) → `uv run tuinscan <stap> --config mijn.yaml`. Zonder Tobias-export: `download` → `voorbeeld` → `tobias.bestand: voorbeeld_export.xlsx` → `inspecteer`.

## Open
- Task 6 (laptop): `/vsicurl/` en PDOK achter de bedrijfsproxy; QGIS-check van uitlijning voor de echte wijk.
- Task 16 (laptop): echte run met de Tobias-export; kolommen.yaml controleren.
- Smoke (cloud, echte PDOK, Houten 300x300 m, 40 voorbeeld-VHE's): download ~80 s; tuinen/score/rapport samen < 30 s; koppeling+tuinvlak 100 % bij `[voor]` en `[voor, achter]`; scores 0-52,8 (mediaan 16, 38 van 40 > 0). Amsterdam-centrum gaf 5 %: dichte binnenstad, geen voortuinen. Contouren in de detailbeelden liggen op de juiste kavels; dak en BAG-pand liggen enkele meters uit elkaar (luchtfoto-scheefstand), zie QGIS-check.

## Belangrijkste beslissingen
- Tobias-kolomnamen onbekend: code kent alleen canonieke namen; mapping in `invoer/kolommen.yaml` (door `inspecteer` voorgesteld op waardepatronen).
- Complex-perceelsplitsing: Voronoi op huiszaden; correcties via `invoer/tuincorrecties.gpkg`. Voor/achter via richting naar dichtstbijzijnde BGT-wegdeel; `tuin.delen` standaard `[voor]`.
- Ontbrekend signaal = 0 bijdrage + vlag "ontbrekend" (score wordt nooit opgeblazen).
- Vegetatiemasker per pixel: zomer-NDVI waar CIR geldig is, anders ExG. AHN alleen actueel op PDOK (geen vorige versie).
- R5: een tuindeel dat de lijn door het pandzwaartepunt (loodrecht op de voorzijde-vector) kruist, wordt vóór het voor/achter-labelen doorgesneden.
- R6: complex-perceel wordt gesplitst langs verlengde pandscheidingen waar aangrenzende panden elk één woon-vbo hebben (spec: loodrecht op de BAG-pandscheidingen); Voronoi alleen anders.
- R7: S1/S5 gebruiken NDVI alleen als beide zomers CIR hebben, anders ExG voor beide jaren.
- R8: S4a laat de standaardberging vallen vóór de vorige-AHN-logica en bepaalt "nieuw" per pixel (geen vorige AHN op PDOK: alle pixels nieuw).

## Gedrag
- `alles` stopt bij een stapfout of mislukte download (exit 1); fouten per adres stoppen niet.
- `opruimen` vraagt de rondenaam, controleert alle doelen vóór het verwijderen (binnen invoer/cache/uitvoer), verwijdert symlinks alleen als link, bewaart `kolommen.yaml` en `tuincorrecties.gpkg` alleen met `--bewaar-correcties`.
- `voorbeeld` schrijft `invoer/voorbeeld_export.xlsx` (nooit de echte export); alleen panden met precies één woon-vbo.
