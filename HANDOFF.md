# HANDOFF — Tuinscan

## Status (2026-11-25)
- PoC code klaar (plan-taken 1–15 + eindreview-fixes + "best of both"-patch). `uv run pytest -q`: 348 passed; `uv run ruff check .` schoon. Branch `claude/inspiring-bardeen-sbgqro`.
- Plan: `docs/superpowers/plans/2026-11-25-tuinscan-poc.md`. PDOK-feiten + kalibratie: `docs/pdok-bronnen.md`. Gebruik: `README.md` (volledig, NL) en `docs/HANDLEIDING.md` (korte handleiding).
- Spec (Claude Docs "Specsheet Tuinscan") bijgewerkt met de gemeten PDOK-feiten (zomer-CIR bestaat).

## Draaien
`uv sync` → `config/tuinscan.example.yaml` kopiëren (data buiten OneDrive) → `download` → `kalibreer` (drempels per laag invullen) → `tuinen` (check in QGIS) → `score` → `rapport` → `terugkoppeling maak|lees`. Zonder Tobias-export: `voorbeeld` na `download`.

## Testresultaten echte PDOK (cloud)
- Houten-rijtjeswijk, 80 VHE's, voor+achter: koppeling+tuinvlak 100 %; download ~80 s, score 8 s (≈35 s voor 300 VHE's).
- Na kalibratie als groen geteld: wegen 4/2 %, daken 9/5 %, BGT-groen 84/89 % (2025/2024). Top-3 handmatig bekeken: redenen plausibel.
- Zomerfoto's zijn geen true-ortho: daken hellen 0,5–2 m (Houten) tot 3,5–5 m (Amsterdam) → marges rond panden in config.
- Amsterdam-centrum: 1 van 20 VHE's kreeg een tuin (dichte binnenstad zonder voortuinen); geen fout.

## Recent (best-of-both-patch, 2026-11-25)
- Overgenomen: één standaardberging per VHE (niet voor én achter), `*`-regel telt niet als VHE, atomaire schrijfacties, mislukte downloadlaag → exit 1, Retry-After + next-link-lusbeveiliging, extra configchecks, rasters één keer per stap open + DTM één keer per wijk gevuld, WMTS-mozaïek direct naar schijf, anker per woningdeel bij meergezinspand, veilige uitsnedenamen + oude uitsneden opruimen, terugkoppelbestand valideert blad/kolommen.
- Nieuw: `signalen.s3_max_hoogte_m` (standaard 4.5, 0 = uit) — S3 negeert straatboomkruinen. Optionele sleutel (enige met standaardwaarde). Verandert scores + parameterset-hash.
- Niet overgenomen: rijbaan-voorrang bij voor/achter (zou in hofjes-/bloemkoolwijken met voordeur aan voetpad en parkeerhof achter juist omklappen; `pdok.rijbaan_functies` zit niet in de parameterset-hash).
- Eigen aanpassingen: terugkoppelbestand zet tekst-/datumformaat op kolomniveau (lege rijen erven het, geen 1000 lege rijen); opruimen oude uitsneden overleeft een vergrendeld bestand (Windows).

## Open
- S3-cap 4,5 m valideren op de echte wijk (top-3 vóór/na vergelijken).
- Task 6 (laptop): PDOK en `/vsicurl/` achter de bedrijfsproxy; QGIS-check uitlijning voor de echte wijk.
- Task 16 (laptop): echte Tobias-export → `inspecteer` → `kolommen.yaml` controleren → volledige run; `kalibreer` op de eigen wijk; ~10 tuinen in QGIS bekijken vóór de top-20 naar de wijkbeheerder gaat; daarna parameterset bevriezen.
- Bestaande eigen configs: 6 nieuwe verplichte sleutels (`pdok.rijbaan_functies` + 5 `signalen.*`); de foutmelding noemt ze.
- Kleine restpunten: uitvoerbestanden worden na elkaar vervangen (staat de foutenlijst open in Excel, dan blijft die oud en is de rest nieuw → sluiten en opnieuw draaien); voor/achter kan omklappen bij een achterpad dat in BGT als voetpad staat (QGIS-check).

## Belangrijkste beslissingen
- Tobias-kolomnamen onbekend → alleen canonieke namen in code; mapping in `invoer/kolommen.yaml` (voorstel door `inspecteer` op waardepatronen).
- Begroeiingsmasker per pixel: zomer-NDVI waar CIR geldig is, anders ExG (0,12); drempels per fotolaag via `kalibreer`; scoreschaal blijft vast.
- Ontbrekend signaal = 0 bijdrage + vlag "ontbrekend"; herkomst (jaren, lagen, AHN-kaartbladen, parameterset) komt uit de cache-manifest.
- R5: tuindeel dat de lijn door het pandmidden (loodrecht op de voorzijde) kruist, wordt daar doorgesneden vóór voor/achter-labeling.
- R6: complexperceel splitsen langs verlengde pandscheidingen waar aangrenzende panden elk één woon-vbo hebben; Voronoi alleen als terugval.
- R7: S1/S5 gebruiken NDVI alleen als beide zomers CIR hebben, anders ExG voor beide jaren.
- R8: S4a haalt eerst de standaardberging weg en beslist "nieuw" per pixel (PDOK heeft geen vorige AHN → alles nieuw); plus pandmarge 1 m, groenmarge 1 m en max hoogte 4,5 m tegen scheve daken en boomkruinen.

## Gedrag
- `alles` stopt bij een stapfout of mislukte download (exit 1); fouten per adres stoppen niet. `kalibreer` zit niet in `alles`.
- `opruimen` vraagt de rondenaam, verwijdert alleen cache-onderdelen, de ronde-map (met kenmerkbestand) en de exports; `kolommen.yaml` blijft, `tuincorrecties.gpkg` alleen met `--bewaar-correcties`. Andere ronde-mappen blijven staan (bevatten adressen).
- `voorbeeld` schrijft `invoer/voorbeeld_export.xlsx` (nooit de echte export); alleen panden met precies één woon-vbo.
