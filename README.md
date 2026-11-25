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

## Eerste keer: de stappen

Elk commando heeft `--config mijn.yaml` nodig (`uv run tuinscan <stap> --config mijn.yaml`).

| Stap | Wat het doet |
| --- | --- |
| `inspecteer` | Leest de Tobias-export en schrijft een voorstel voor `invoer/kolommen.yaml`. Controleer en pas dit bestand aan. |
| `download` | Haalt BAG, BRK, BGT, luchtfoto's en AHN op voor het werkgebied (alleen bbox-verzoeken). |
| `tuinen` | Koppelt VHE aan woning, pand en perceel en bepaalt het tuinvlak. Controleer `uitvoer/<ronde>/tuinen.gpkg` in QGIS. Bijsturen kan met `invoer/tuincorrecties.gpkg`. |
| `score` | Berekent de signalen en de score per VHE. |
| `rapport` | Maakt `rapport.html` (ook offline te openen) met uitsneden. |
| `terugkoppeling maak` | Maakt `terugkoppeling.xlsx` met keuzelijsten voor de bezoeken. |
| `terugkoppeling lees` | Leest het ingevulde bestand en schrijft `kwaliteit.csv`. |
| `opruimen` | Verwijdert cache, uitvoer van de ronde en de exportbestanden. |

Handig:

- `uv run tuinscan alles --config mijn.yaml` doet download tot en met `terugkoppeling maak` in een keer en toont de tijd per stap.
- Nog geen Tobias-export? Draai eerst `download`, dan `voorbeeld --aantal 200`. Dat maakt `invoer/voorbeeld_export.xlsx` (nooit de echte export). Zet `tobias.bestand` in je config op `voorbeeld_export.xlsx` en draai `inspecteer`.
- Een enkel adres dat faalt stopt de stap niet; zie `foutenlijst.csv` (VHE-nummer, stap, reden).
- `opruimen` vraagt om de rondenaam. Met `--bewaar-correcties` blijft `tuincorrecties.gpkg` staan; `kolommen.yaml` blijft altijd staan.

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
