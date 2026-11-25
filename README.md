# Tuinscan

Lokale Python-CLI die tuinen van huurwoningen rangschikt op basis van open PDOK-geodata (BAG, BRK, BGT, luchtfoto, AHN). Uitkomst: een HTML-rapport met een top-lijst, redenen per tuin en een Excel-terugkoppelbestand voor de tuinbezoeken.

## Installeren (zonder beheerdersrechten)

1. Installeer [uv](https://docs.astral.sh/uv/) voor alleen jouw gebruiker. Open PowerShell en voer uit:
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   Alternatief: Python 3.12 of 3.13 van python.org, kies "Install for me only".
2. In de projectmap: `uv sync`
3. Test: `uv run tuinscan --help`

## Mappen inrichten (buiten OneDrive)

De data staat nooit in de Git-map en nooit in OneDrive. Tuinscan weigert te draaien als een van de drie mappen in een OneDrive-map staat.

```
C:\TuinscanData\invoer\    Tobias-export, kolommen.yaml, tuincorrecties.gpkg (optioneel)
C:\TuinscanData\cache\     gedownloade PDOK-data
C:\TuinscanData\uitvoer\   per ronde: rapport.html, scores, terugkoppeling.xlsx, ...
```

Kopieer `config/tuinscan.example.yaml` naar een eigen bestand (bijv. `mijn.yaml`, niet committen) en pas aan: `gebied` (naam en `bbox_rd` in RD-meters), `mappen` en `tobias.bestand`. Alle gewichten, drempels en filters staan in deze config, niet in de code.

- **Wijk kiezen:** de Tobias-export bevat meestal het hele bezit. Zet `tobias.filters.wijk` op de wijk(en) binnen `bbox_rd` (precies zoals in de export, bijv. `wijk: [Centrum-Oost]`). Anders komen alle VHE's buiten het werkgebied in de foutenlijst ("geen tuinvlak").
- **Adres en optionele velden komen uit Tobias:** adres (of straat + huisnummer), wijk, complex, woningtype en de optionele velden `eigen_tuin`, `standaardberging`, `status` en `sloop_renovatie` worden alleen uit de export gelezen, via `kolommen.yaml`. Niet gekoppeld = filter op dat veld wordt overgeslagen (met een waarschuwing).

## Eerste keer: de stappen

Elk commando heeft `--config mijn.yaml` nodig (`uv run tuinscan <stap> --config mijn.yaml`).

| Stap | Wat het doet |
| --- | --- |
| `inspecteer` | Leest de Tobias-export en schrijft een voorstel voor `invoer/kolommen.yaml`. Controleer en pas dit bestand aan. |
| `download` | Haalt BAG, BRK, BGT, luchtfoto's en AHN op voor het werkgebied (alleen bbox-verzoeken). Wijzig je een laag, zoom of URL in de config, dan haalt `download` dat onderdeel opnieuw op. |
| `kalibreer` | Meet ExG/NDVI per zomerfoto op wegen, daken en openbaar groen en stelt per laag een begroeiingsdrempel voor (zie hieronder). |
| `tuinen` | Koppelt VHE aan woning, pand en perceel en bepaalt het tuinvlak. Controleer `uitvoer/<ronde>/tuinen.gpkg` in QGIS. Bijsturen kan met `invoer/tuincorrecties.gpkg`. |
| `score` | Berekent de signalen en de score per VHE. |
| `rapport` | Maakt `rapport.html` (ook offline te openen) met uitsneden. |
| `terugkoppeling maak` | Maakt `terugkoppeling.xlsx` met keuzelijsten voor de bezoeken. |
| `terugkoppeling lees` | Leest het ingevulde bestand en schrijft `kwaliteit.csv`. |
| `opruimen` | Verwijdert de cache-onderdelen van tuinscan, de uitvoer van deze ronde en de exportbestanden. |

### Begroeiingsdrempels kalibreren (na `download`, vóór `score`)

De zomerfoto's van PDOK verschillen per vlucht in kleur en helderheid; een vaste drempel ziet dan in het ene jaar wegen en daken als groen en in het andere jaar niet. Daarom:

1. `uv run tuinscan kalibreer --config mijn.yaml` toont per zomerlaag de verdeling (p5–p95) van ExG en NDVI op BGT-rijbaan, voetpad, BAG-daken en BGT-groen, het aandeel dat nu als groen telt en een voorstel, bijv. `luchtfoto.lagen.cir_nu.ndvi_drempel: 0.26`.
2. Zet de voorstellen in je config bij de laag zelf, bijv. `cir_nu: {laag: 2025_ortho25IR, jaar: 2025, zoom: "14", ndvi_drempel: 0.26}` (NDVI bij `cir_nu`/`cir_vorig`, ExG bij `zomer_nu`/`zomer_vorig`).
3. Draai `kalibreer` opnieuw: rijbaan en daken horen hooguit ~10 % groen te zijn, BGT-groen minstens ~85 %. Dan `score`.

De drempels per laag tellen mee in de parameterset (zie het rapport). Begroeiing = NDVI waar de infraroodfoto geldig is; ExG (`signalen.exg_drempel`) alleen waar die ontbreekt.

Let op: de zomerfoto is niet loodrecht. Daken kunnen meters over de tuin vallen; daarom tellen pixels vlak naast een BAG-pand niet mee voor groentoename en verharding (`signalen.foto_pandmarge_m`) en negeert "hoog object" de hoogterand langs panden (`signalen.s4a_pandmarge_m`).

Handig:

- `uv run tuinscan alles --config mijn.yaml` doet download tot en met `terugkoppeling maak` in een keer en toont de tijd per stap (zonder `kalibreer`: die draai je bij een nieuw werkgebied of nieuwe luchtfoto's eerst los). Mislukt een stap, of mislukt een downloadonderdeel (`mislukt` in de uitvoer van `download`), dan stopt `alles` met een foutmelding.
- Nog geen Tobias-export? Draai eerst `download`, dan `voorbeeld --aantal 200`. Dat maakt `invoer/voorbeeld_export.xlsx` (nooit de echte export). Zet `tobias.bestand` in je config op `voorbeeld_export.xlsx` en draai `inspecteer`.
- Een enkel adres dat faalt stopt de stap niet; zie `foutenlijst.csv` (VHE-nummer, stap, reden). `foutenlijst.csv` en `kwaliteit.csv` gebruiken een puntkomma en openen direct goed in Nederlandse Excel. Staat er een regel met VHE `*` in, dan geldt die voor de hele export (bijv. BAG-id's die Excel heeft afgerond: exporteer die kolom als tekst).
- Elke stap schrijft een logbestand `uitvoer/<ronde>/tuinscan.log` (zonder adressen); de console toont alleen waarschuwingen en fouten.
- Staat een uitvoerbestand open in QGIS of Excel, dan stopt de stap met "Sluit bestand ... en probeer opnieuw"; de bestaande bestanden blijven dan ongewijzigd.
- `opruimen` vraagt om de rondenaam. Het verwijdert alleen wat tuinscan maakte: in de cache `manifest.json`, `vector/` en `raster/`, de map van deze ronde (alleen als daar `ronde.json`, `vhe.parquet` of `tuinscan.log` in staat), de export en `voorbeeld_export.xlsx`. Met `--bewaar-correcties` blijft `tuincorrecties.gpkg` staan; `kolommen.yaml` blijft altijd staan. **Mappen van andere rondes in `uitvoer` blijven staan en bevatten ook adressen**: verwijder die zelf, of draai `opruimen --ronde <naam>` per ronde.
- Rondenamen (`--ronde` of `gebied.naam`) zijn mapnamen: geen `\ / : * ? " < > |`, geen punt of spatie aan het eind en geen Windows-namen als `CON`, `NUL` of `COM1`.

## Privacyregels

- Er gaan **geen adressen of adrescoördinaten** naar PDOK: alleen verzoeken voor het hele werkgebied (bbox).
- Geen huurdersgegevens: alleen VHE-nummer, BAG-id, wijk, complex en woningtype uit Tobias.
- Terugkoppeling gebruikt vaste keuzelijsten, geen vrije tekst.
- Data (`invoer`, `cache`, `uitvoer`) staat buiten Git en buiten OneDrive; deel rapporten alleen binnen de afgesproken kring.
- Het rapport is een hulpmiddel om bezoeken te plannen, geen oordeel over huurders.

## Ontwikkelen en testen

```
uv sync
uv run pytest
uv run ruff check .
```

De tests zijn offline: ze draaien op een synthetische mini-wijk (`tests/miniwijk.py`). PDOK-bronnen en meetwaarden staan in `docs/pdok-bronnen.md`.
