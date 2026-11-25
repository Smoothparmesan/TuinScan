# HANDOFF — Tuinscan

## Status (2026-11-25)
- Alle code-taken (1-15) klaar; `uv run pytest` en `uv run ruff check .` groen. Plan: `docs/superpowers/plans/2026-11-25-tuinscan-poc.md`.
- Commando's: `inspecteer`, `download`, `voorbeeld`, `tuinen`, `score`, `rapport`, `terugkoppeling maak|lees`, `alles`, `opruimen`. Zie README.md.

## Draaien
`uv sync` → config kopiëren uit `config/tuinscan.example.yaml` (data buiten OneDrive) → `uv run tuinscan <stap> --config mijn.yaml`. Zonder Tobias-export: `download` → `voorbeeld` → `tobias.bestand: voorbeeld_export.xlsx` → `inspecteer`.

## Open
- Task 6 (laptop): `/vsicurl/` en PDOK achter de bedrijfsproxy; QGIS-check van uitlijning voor de echte wijk.
- Task 16 (laptop): echte run met de Tobias-export; kolommen.yaml controleren.
- Smoke-run (cloud, Amsterdam-centrum, 200x200 m): koppeling 100 %, maar maar 1 van 20 VHE's kreeg een voortuin; de "grondgebonden"-heuristiek van `voorbeeld` is in dichte binnenstad grof. In een gewone woonwijk opnieuw beoordelen.

## Belangrijkste beslissingen
- Tobias-kolomnamen onbekend: code kent alleen canonieke namen; mapping in `invoer/kolommen.yaml` (door `inspecteer` voorgesteld op waardepatronen).
- Perceelsplitsing complex: Voronoi op huiszaden; correcties via `invoer/tuincorrecties.gpkg`. Voor/achter via richting naar dichtstbijzijnde BGT-wegdeel; `tuin.delen` standaard `[voor]`.
- Ontbrekend signaal = 0 bijdrage + vlag "ontbrekend" (score wordt nooit opgeblazen).
- PDOK: zomer-CIR bestaat, dus vegetatiemasker = zomer-NDVI OF ExG; AHN alleen actueel, dus S4a "nieuw t.o.v. vorige AHN" niet mogelijk in PoC.
- R5: `alles` stopt bij een stapfout (exit 1), niet bij fouten per adres.
- R6: `voorbeeld` schrijft nooit over de echte export heen; adresvelden komen uit `pdok.velden` (`vbo_straat`, `vbo_huisnummer`, ...).
- R7: `opruimen` vraagt de rondenaam via `input()`, verwijdert alleen binnen de drie databomen (assert) en bewaart `kolommen.yaml`.
- R8: `tuincorrecties.gpkg` wordt alleen met `--bewaar-correcties` behouden.
