# Tuinscan — Short Manual

Tuinscan ranks the gardens of rental homes (VHE's) using open PDOK geodata (BAG, BRK, BGT, aerial photos, AHN). It produces an offline HTML report with a top list and reasons per garden, plus an Excel feedback file for garden visits.

Full reference (Dutch): `README.md`. PDOK sources and calibration data: `docs/pdok-bronnen.md`.

---

## 1. Install (no admin rights needed)

1. **Install uv** for your user only (PowerShell):
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
2. **Install dependencies** in the project folder:
   ```powershell
   uv sync
   ```
3. **Check it works:**
   ```powershell
   uv run tuinscan --help
   ```

## 2. Set up folders (outside OneDrive)

Data never goes in the Git folder or in OneDrive — Tuinscan refuses to run if it does.

```
C:\TuinscanData\invoer\    Tobias export, kolommen.yaml, tuincorrecties.gpkg (optional)
C:\TuinscanData\cache\     downloaded PDOK data
C:\TuinscanData\uitvoer\   one folder per round: report, scores, feedback file
```

Put the Tobias export (`.xlsx`) in `invoer\`.

## 3. Configure

1. **Copy** `config/tuinscan.example.yaml` to e.g. `mijn.yaml` (do not commit it).
2. **Edit** these keys:

| Key | Set to |
| --- | --- |
| `gebied.naam` | Round name (used as folder name) |
| `gebied.bbox_rd` | Work area `[xmin, ymin, xmax, ymax]` in RD metres (EPSG:28992) |
| `mappen` | The three folders from step 2 |
| `tobias.bestand` | File name of the Tobias export in `invoer\` |
| `tobias.filters.wijk` | Neighbourhood name(s) inside the bbox, exactly as in the export |

All weights, thresholds and filters live in the config, not in the code. A missing required key gives an error that names it.

## 4. First run in a new area

Every command needs `--config mijn.yaml`. Optional: `--ronde <name>` (defaults to `gebied.naam`).

1. **Map the export columns**
   ```powershell
   uv run tuinscan inspecteer --config mijn.yaml
   ```
   Check and fix the proposal in `invoer\kolommen.yaml`.
2. **Download PDOK data** (bbox requests only; ~80 s per 80 homes)
   ```powershell
   uv run tuinscan download --config mijn.yaml
   ```
   If any layer reports `mislukt` (failed), fix it before continuing.
3. **Calibrate vegetation thresholds**
   ```powershell
   uv run tuinscan kalibreer --config mijn.yaml
   ```
   Copy each proposed threshold into the config **on the layer itself**, e.g.
   `cir_nu: {laag: 2025_ortho25IR, jaar: 2025, zoom: "14", ndvi_drempel: 0.26}`.
   Run again until roads and roofs are ≤ ~10 % green and BGT green is ≥ ~85 %.
4. **Determine gardens**
   ```powershell
   uv run tuinscan tuinen --config mijn.yaml
   ```
   **Open `uitvoer\<round>\tuinen.gpkg` in QGIS** and check ~10 gardens. Fix wrong ones by drawing the correct polygon in `invoer\tuincorrecties.gpkg` (layer `tuinvlak`, columns `vhe_nummer` and `deel` = `voor`/`achter`, EPSG:28992), then rerun `tuinen`.
5. **Score, report, feedback file**
   ```powershell
   uv run tuinscan score --config mijn.yaml
   ```
   ```powershell
   uv run tuinscan rapport --config mijn.yaml
   ```
   ```powershell
   uv run tuinscan terugkoppeling maak --config mijn.yaml
   ```

**Later rounds** (same area, thresholds already set): one command runs download → tuinen → score → rapport → terugkoppeling maak:
```powershell
uv run tuinscan alles --config mijn.yaml
```

**No Tobias export yet?** After `download`, make a sample export from BAG, set `tobias.bestand: voorbeeld_export.xlsx`, then run `inspecteer`:
```powershell
uv run tuinscan voorbeeld --config mijn.yaml --aantal 200
```

## 5. Use the results

All output is in `uitvoer\<round>\`:

| File | Use |
| --- | --- |
| `rapport.html` | Top list with score, reasons and image cut-outs. Opens offline. |
| `terugkoppeling.xlsx` | Fill in during visits: verdict, cause, action (drop-down lists only). |
| `foutenlijst.csv` | Homes that failed (VHE, step, reason). Opens directly in Dutch Excel. |
| `tuinscan.log` | Run log (no addresses). |

**What the score is made of** (weights in `score:` in the config):

| Signal | Meaning |
| --- | --- |
| S1 | Increase in green (this summer vs last summer) |
| S2 | Share of tall vegetation (> 2 m) |
| S3 | Vegetation overhanging the boundary (m²) |
| S4a | New tall, non-green objects, e.g. sheds or structures (m²) |
| S5 | Paved / non-green area in both summers |

A missing signal counts as 0 and is flagged "ontbrekend" in the report.

**After the visits:** read the filled-in Excel back to measure precision per signal:
```powershell
uv run tuinscan terugkoppeling lees --config mijn.yaml
```
This writes `kwaliteit.csv`. Use it to tune weights and thresholds for the next round.

## 6. Clean up

Removes Tuinscan's cache, this round's output folder and the export files (asks for the round name):
```powershell
uv run tuinscan opruimen --config mijn.yaml
```
- `--bewaar-correcties` keeps `tuincorrecties.gpkg`; `kolommen.yaml` is always kept.
- **Other round folders are not removed and contain addresses** — remove them yourself or run `opruimen --ronde <name>` per round.

## 7. Troubleshooting

| Message / symptom | Fix |
| --- | --- |
| "Sluit bestand … en probeer opnieuw" | Close the file in Excel/QGIS and rerun the step. |
| Many VHE's "geen tuinvlak" in `foutenlijst.csv` | Set `tobias.filters.wijk` to the area inside the bbox. |
| A `*` line in `foutenlijst.csv` | Applies to the whole export, e.g. BAG ids rounded by Excel → export that column as text. |
| Roads or roofs counted as green | Rerun `kalibreer` and update the per-layer thresholds. |
| Download fails on the work laptop | Check the company proxy allows PDOK (`api.pdok.nl`, `service.pdok.nl`). |
| Round name refused | No `\ / : * ? " < > \|`, no trailing dot/space, no `CON`, `NUL`, `COM1`. |

## 8. Privacy rules

- No addresses or address coordinates are sent to PDOK — only whole-area (bbox) requests.
- Only VHE number, BAG id, wijk, complex and woningtype are read from Tobias; no tenant data.
- Feedback uses fixed drop-down lists, no free text.
- Share reports only within the agreed group. The report helps plan visits; it is not a judgement of tenants.

## 9. Developers

```powershell
uv sync
```
```powershell
uv run pytest -q
```
```powershell
uv run ruff check .
```
Tests are offline and run on a synthetic mini neighbourhood (`tests/miniwijk.py`).
