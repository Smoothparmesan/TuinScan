# Tuinscan — Korte handleiding

Tuinscan rangschikt de tuinen van huurwoningen (VHE's) op basis van open PDOK-geodata (BAG, BRK, BGT, luchtfoto's, AHN). Uitkomst: een offline HTML-rapport met een top-lijst en redenen per tuin, plus een Excel-terugkoppelbestand voor de tuinbezoeken.

Volledige naslag: `README.md`. PDOK-bronnen en kalibratiegegevens: `docs/pdok-bronnen.md`.

---

## 1. Installeren (zonder beheerdersrechten)

1. **Installeer uv** voor alleen jouw gebruiker (PowerShell):
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
2. **Installeer de afhankelijkheden** in de projectmap:
   ```powershell
   uv sync
   ```
3. **Controleer of het werkt:**
   ```powershell
   uv run tuinscan --help
   ```

## 2. Mappen inrichten (buiten OneDrive)

Data staat nooit in de Git-map en nooit in OneDrive; Tuinscan weigert dan te draaien.

```
C:\TuinscanData\invoer\    Tobias-export, kolommen.yaml, tuincorrecties.gpkg (optioneel)
C:\TuinscanData\cache\     gedownloade PDOK-data
C:\TuinscanData\uitvoer\   per ronde een map: rapport, scores, terugkoppelbestand
```

Zet de Tobias-export (`.xlsx`) in `invoer\`.

## 3. Configureren

1. **Kopieer** `config/tuinscan.example.yaml` naar bijv. `mijn.yaml` (niet committen).
2. **Pas** deze sleutels aan:

| Sleutel | Invullen |
| --- | --- |
| `gebied.naam` | Rondenaam (wordt de mapnaam) |
| `gebied.bbox_rd` | Werkgebied `[xmin, ymin, xmax, ymax]` in RD-meters (EPSG:28992) |
| `mappen` | De drie mappen uit stap 2 |
| `tobias.bestand` | Bestandsnaam van de Tobias-export in `invoer\` |
| `tobias.filters.wijk` | Wijknaam/-namen binnen de bbox, precies zoals in de export |

Alle gewichten, drempels en filters staan in de config, niet in de code. Ontbreekt een verplichte sleutel, dan noemt de foutmelding die.

## 4. Eerste run in een nieuw gebied

Elk commando heeft `--config mijn.yaml` nodig. Optioneel: `--ronde <naam>` (standaard `gebied.naam`).

1. **Kolommen van de export koppelen**
   ```powershell
   uv run tuinscan inspecteer --config mijn.yaml
   ```
   Controleer en corrigeer het voorstel in `invoer\kolommen.yaml`.
2. **PDOK-data downloaden** (alleen bbox-verzoeken; ~80 s per 80 woningen)
   ```powershell
   uv run tuinscan download --config mijn.yaml
   ```
   Meldt een laag `mislukt`, los dat eerst op.
3. **Begroeiingsdrempels kalibreren**
   ```powershell
   uv run tuinscan kalibreer --config mijn.yaml
   ```
   Zet elke voorgestelde drempel in de config **bij de laag zelf**, bijv.
   `cir_nu: {laag: 2025_ortho25IR, jaar: 2025, zoom: "14", ndvi_drempel: 0.26}`.
   Herhaal tot rijbaan en daken hooguit ~10 % groen zijn en BGT-groen minstens ~85 %.
4. **Tuinvlakken bepalen**
   ```powershell
   uv run tuinscan tuinen --config mijn.yaml
   ```
   **Open `uitvoer\<ronde>\tuinen.gpkg` in QGIS** en bekijk ~10 tuinen. Verkeerde tuinen herstel je door het juiste vlak te tekenen in `invoer\tuincorrecties.gpkg` (laag `tuinvlak`, kolommen `vhe_nummer` en `deel` = `voor`/`achter`, EPSG:28992). Draai daarna `tuinen` opnieuw.
5. **Score, rapport, terugkoppelbestand**
   ```powershell
   uv run tuinscan score --config mijn.yaml
   ```
   ```powershell
   uv run tuinscan rapport --config mijn.yaml
   ```
   ```powershell
   uv run tuinscan terugkoppeling maak --config mijn.yaml
   ```

**Volgende rondes** (zelfde gebied, drempels al ingesteld): één commando doet download → tuinen → score → rapport → terugkoppeling maak:
```powershell
uv run tuinscan alles --config mijn.yaml
```

**Nog geen Tobias-export?** Maak na `download` een voorbeeld-export uit BAG, zet `tobias.bestand: voorbeeld_export.xlsx` en draai `inspecteer`:
```powershell
uv run tuinscan voorbeeld --config mijn.yaml --aantal 200
```

## 5. De uitkomsten gebruiken

Alle uitvoer staat in `uitvoer\<ronde>\`:

| Bestand | Gebruik |
| --- | --- |
| `rapport.html` | Top-lijst met score, redenen en uitsneden. Opent offline. |
| `terugkoppeling.xlsx` | Invullen bij het bezoek: oordeel, oorzaak, actie (alleen keuzelijsten). |
| `foutenlijst.csv` | Woningen die mislukten (VHE, stap, reden). Opent direct in Nederlandse Excel. |
| `tuinscan.log` | Logbestand (zonder adressen). |

**Waaruit de score bestaat** (gewichten onder `score:` in de config):

| Signaal | Betekenis |
| --- | --- |
| S1 | Groentoename (deze zomer t.o.v. vorige zomer) |
| S2 | Aandeel hoge begroeiing (> 2 m) |
| S3 | Begroeiing die over de erfgrens hangt (m²) |
| S4a | Nieuwe hoge, niet-groene objecten, bijv. schuurtjes of bouwwerken (m²) |
| S5 | Verhard / niet-groen oppervlak in beide zomers |

Een ontbrekend signaal telt als 0 en krijgt in het rapport de vlag "ontbrekend".

**Na de bezoeken:** lees het ingevulde Excel-bestand terug om de precisie per signaal te meten:
```powershell
uv run tuinscan terugkoppeling lees --config mijn.yaml
```
Dit schrijft `kwaliteit.csv`. Gebruik dat om gewichten en drempels voor de volgende ronde bij te stellen.

## 6. Opruimen

Verwijdert de cache van Tuinscan, de uitvoermap van deze ronde en de exportbestanden (vraagt om de rondenaam):
```powershell
uv run tuinscan opruimen --config mijn.yaml
```
- `--bewaar-correcties` laat `tuincorrecties.gpkg` staan; `kolommen.yaml` blijft altijd staan.
- **Mappen van andere rondes blijven staan en bevatten adressen**: verwijder die zelf of draai `opruimen --ronde <naam>` per ronde.

## 7. Problemen oplossen

| Melding / symptoom | Oplossing |
| --- | --- |
| "Sluit bestand … en probeer opnieuw" | Sluit het bestand in Excel/QGIS en draai de stap opnieuw. |
| Veel VHE's "geen tuinvlak" in `foutenlijst.csv` | Zet `tobias.filters.wijk` op de wijk binnen de bbox. |
| Regel met VHE `*` in `foutenlijst.csv` | Geldt voor de hele export, bijv. BAG-id's die Excel heeft afgerond → exporteer die kolom als tekst. |
| Wegen of daken tellen als groen | Draai `kalibreer` opnieuw en werk de drempels per laag bij. |
| Download mislukt op de werklaptop | Controleer of de bedrijfsproxy PDOK doorlaat (`api.pdok.nl`, `service.pdok.nl`). |
| Rondenaam geweigerd | Geen `\ / : * ? " < > \|`, geen punt of spatie aan het eind, geen `CON`, `NUL`, `COM1`. |

## 8. Privacyregels

- Er gaan geen adressen of adrescoördinaten naar PDOK, alleen verzoeken voor het hele werkgebied (bbox).
- Uit Tobias worden alleen VHE-nummer, BAG-id, wijk, complex en woningtype gelezen; geen huurdersgegevens.
- Terugkoppeling gebruikt vaste keuzelijsten, geen vrije tekst.
- Deel rapporten alleen binnen de afgesproken kring. Het rapport helpt bezoeken plannen; het is geen oordeel over huurders.

## 9. Ontwikkelaars

```powershell
uv sync
```
```powershell
uv run pytest -q
```
```powershell
uv run ruff check .
```
De tests zijn offline en draaien op een synthetische mini-wijk (`tests/miniwijk.py`).
