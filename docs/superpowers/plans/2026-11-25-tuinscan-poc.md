# Tuinscan PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local Python CLI that, for one wijk (100–300 grondgebonden woningen), turns a Tobias Excel export plus open PDOK data into a ranked signaallijst (offline HTML + GeoPackage) and reads back wijkbeheer feedback to compute precision per signal.

**Architecture:** One package `tuinscan` with a CLI of independently re-runnable steps (`download`, `tuinen`, `score`, `rapport`, `terugkoppeling`, plus `inspecteer`, `voorbeeld`, `alles`, `opruimen`). Each step reads the previous step's files from disk. PDOK data is fetched once per werkgebied bbox into a local cache (no per-address requests); all analysis runs per tuin on a common 0.25 m grid in EPSG:28992. All tunables live in one YAML config.

**Tech Stack:** Python 3.12, uv, geopandas, shapely 2, pyogrio, rasterio, numpy, pandas, openpyxl, pyyaml, jinja2, requests, pillow (added: JPEG thumbnails/outlines). Dev: pytest, responses, ruff.

**Spec:** "Specsheet Tuinscan – voorselectie adressen voor wijkbeheer" — https://claude.ai/artifact/H4Wmv6QW3E4iU3wH7tZ94X, section *Proof of concept (lokaal)* plus *Selectiecriteria en scoring*, *Databronnen*, *Privacy*. Executors read both.

## Global Constraints

- Runs on a Windows work laptop **without admin rights** (N07): Python via uv or python.org "Install for me only". Paths via `pathlib` only; no symlinks, no shell-specific calls.
- Data folders (`invoer`, `cache`, `uitvoer`) must be **outside OneDrive sync**; the tool refuses to run otherwise.
- **No addresses or address coordinates go to PDOK** (N03): only bbox-based requests for the werkgebied (OGC API bbox, WMTS tiles covering the whole bbox, COG windows for the whole bbox).
- No tenant data. Feedback uses fixed lists only, no free-text field (F10).
- CRS everywhere: EPSG:28992. Analysis grid 0.25 m.
- One failing address never stops a step (N06); it goes to `foutenlijst.csv` with `vhe_nummer, stap, reden`.
- Every score row carries `zomer_jaar, zomer_vorig_jaar, winter_jaar, ahn_versie, parameterset` (N05).
- Weights, bounds, thresholds, filters, column mapping: config only, never code (N04). All numeric values in this plan are **startwaarden**.
- Signals in PoC: S1, S2, S3, S4a, S5. S4b is out. Enabled weights must sum to 100.
- Code and config in Git; data folders never (`.gitignore`).
- **PDOK facts are verified** in `docs/pdok-bronnen.md` (2026-11-25); config values below come from there.
- **Deviation from spec (evidence in `docs/pdok-bronnen.md`)**: PDOK serves summer CIR (`YYYY_ortho25IR`, leaf-on). The vegetation mask therefore uses **summer NDVI** (primary) OR summer ExG; winter CIR is not used in the PoC. Winter RGB (`orthoHR`) is used only for detail images.
- **Tobias column names are unknown**: the code addresses Tobias fields only through canonical names mapped in `tobias.kolommen`; no Tobias header string appears in code.

## Review Focus

1. **BAG-id stored as an Excel number** (leading zero dropped, or float like `3.63010000123456e+15`): normalise to a 16-digit string; if it still fails validation, the VHE goes to the foutenlijst with the hint "BAG-id mogelijk afgerond door Excel; exporteer de kolom als tekst". Test in Task 2.
2. **Messy export rows**: duplicate VHE numbers, empty BAG-id, two VHEs on one verblijfsobject, header not on row 1. Expect: first duplicate kept + others in foutenlijst, header auto-detected, run continues. Tests in Tasks 2 and 7.
3. **Missing imagery**: a tuin partly outside the downloaded area, a missing tile (nodata), or no previous-year layer configured. Expect: affected signal = `None`, listed in `ontbrekend`, contributes 0 (score never inflated), shown in the report. Tests in Tasks 9–11.
4. **Data folder inside OneDrive**: expect a clear refusal before any file is written. Test in Task 1.
5. **Filled-in feedback Excel**: rows re-sorted, blank rows, a value typed outside the dropdown, VHE read back as a number. Expect: matched on VHE, blanks ignored, invalid rows reported per row, rest processed. Test in Task 14.

---

## File Structure

```
pyproject.toml                 uv project, console script `tuinscan = tuinscan.cli:main`
.gitignore                     data folders, .venv, caches
config/tuinscan.example.yaml   full config with startwaarden (committed)
README.md                      install (no admin), workflow, privacy rules
src/tuinscan/
  cli.py                       argparse subcommands → step functions
  config.py                    dataclasses + laad_config + parameterset_hash
  mappen.py                    folder layout, OneDrive guard, ronde folder
  fouten.py                    Foutenlijst collector → csv
  tobias.py                    export reading, header detection, mapping, filters
  inspecteer.py                mapping suggestion from headers + value patterns
  pdok/ogc.py                  OGC API Features paging → GeoDataFrame
  pdok/wmts.py                 capabilities, tile math, mosaic GeoTIFF
  pdok/ahn.py                  kaartbladindex + COG window → GeoTIFF
  pdok/cache.py                manifest.json + cache paths
  download.py                  `download` step orchestration
  koppeling.py                 VHE → vbo → pand → perceel
  tuinvlak.py                  perceel − panden, complex split, voor/achter, correcties
  rasters.py                   common-grid windows, TuinRasters
  signalen.py                  ExG, NDVI, masks, S1–S5
  score.py                     scaling, weighted score, redenen
  uitsneden.py                 thumbnails + detail images
  rapport.py, templates/rapport.html.j2
  terugkoppeling.py            Excel with dropdowns, read-back, kwaliteit
  voorbeeld.py                 synthetic export from BAG (until Tobias delivers)
  stappen.py                   tuinen/score/rapport/alles/opruimen step functions
tests/
  conftest.py                  synthetic mini-wijk (vectors + GeoTIFFs) in tmp_path
  test_*.py                    one per module; test_keten.py end-to-end
```

Data layout (outside the repo, from config `mappen`):
```
invoer/   tobias_export.xlsx, kolommen.yaml (mapping), tuincorrecties.gpkg (optional)
cache/    manifest.json, vector/*.gpkg, raster/*.tif
uitvoer/<ronde>/  vhe.parquet, foutenlijst.csv, tuinen.gpkg, signalen.parquet,
                  scores.parquet, uitsneden/*.jpg, rapport.html,
                  terugkoppeling.xlsx, kwaliteit.csv, tuinscan.log
```

---

### Task 1: Project skeleton, config, folders, OneDrive guard

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `config/tuinscan.example.yaml`, `src/tuinscan/{__init__,cli,config,mappen,fouten}.py`
- Test: `tests/test_config.py`, `tests/test_mappen.py`, `tests/test_fouten.py`

**Interfaces:**
- Produces:
  - `laad_config(pad: Path) -> Config`; raises `ConfigFout(str)` naming the YAML key.
  - `Config` dataclass with sections `gebied, mappen, tobias, pdok, luchtfoto, ahn, tuin, signalen, score, terugkoppeling` (fields as in the example YAML below).
  - `parameterset_hash(cfg: Config) -> str` — first 8 hex of sha256 over canonical JSON of `tuin, signalen, score`.
  - `Mappen.uit_config(cfg, ronde: str | None) -> Mappen` with `.invoer, .cache, .uitvoer, .ronde_dir`; `controleer_buiten_onedrive(paden: list[Path], env: Mapping[str,str]) -> None` raises `OneDriveFout`.
  - `Foutenlijst.voeg_toe(vhe_nummer: str, stap: str, reden: str)`, `.schrijf(pad: Path)`, `.aantal`, `.vhe_nummers() -> set[str]`.
  - `cli.main(argv: list[str] | None = None) -> int`; every subcommand takes `--config PAD` and optional `--ronde NAAM` (default `gebied.naam`).

Example config (key names are binding; values are startwaarden):

```yaml
gebied: {naam: proefwijk, bbox_rd: [0, 0, 0, 0], buffer_m: 50}
mappen: {invoer: C:/TuinscanData/invoer, cache: C:/TuinscanData/cache, uitvoer: C:/TuinscanData/uitvoer}
tobias:
  bestand: tobias_export.xlsx
  blad: null          # null = first sheet
  kopregel: null      # null = auto-detect (1-based row number otherwise)
  kolommen_bestand: kolommen.yaml   # in invoer/, produced by `tuinscan inspecteer`
  filters: {wijk: [], complex: [], woningtype: [], eigen_tuin_ja: [], status_uitsluiten: [], sloop_renovatie_uitsluiten: []}
pdok:
  bag_url: https://api.pdok.nl/kadaster/bag/ogc/v2
  brk_url: https://api.pdok.nl/kadaster/brk-kadastrale-kaart/ogc/v1
  bgt_url: https://api.pdok.nl/lv/bgt/ogc/v1
  collecties: {pand: pand, verblijfsobject: verblijfsobject, perceel: perceel, wegdeel: wegdeel, begroeid: begroeidterreindeel}
  velden:
    vbo_id: identificatie
    vbo_pand_href: pand.href          # list of URLs; last segment = pand feature id
    vbo_gebruiksdoel: gebruiksdoel
    woonfunctie: woonfunctie
    pand_id: identificatie
    pand_aantal_vbo: aantal_verblijfsobjecten
    perceel_id: identificatie_lokaal_id
    wegdeel_functie: functie
    begroeid_fysiek: fysiek_voorkomen
  voetpad_functies: [voetpad, voetgangersgebied, "voetpad op trap"]
  openbaar_groen_fysiek: [groenvoorziening, loofbos, "gemengd bos", naaldbos, struiken, houtwal, "grasland overig"]
  workers: 4
luchtfoto:
  rgb_wmts: https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0
  cir_wmts: https://service.pdok.nl/hwh/luchtfotocir/wmts/v1_0
  matrixset: "EPSG:28992"
  lagen:              # verified 2026-11-25; ortho25(IR) = summer, orthoHR = winter
    zomer_nu: {laag: 2025_ortho25, jaar: 2025, zoom: "14"}
    zomer_vorig: {laag: 2024_ortho25, jaar: 2024, zoom: "14"}
    cir_nu: {laag: 2025_ortho25IR, jaar: 2025, zoom: "14"}      # summer CIR
    cir_vorig: {laag: 2024_ortho25IR, jaar: 2024, zoom: "14"}
    winter_nu: {laag: 2026_orthoHR, jaar: 2026, zoom: "15"}     # detail images only
    winter_vorig: {laag: 2025_orthoHR, jaar: 2025, zoom: "15"}
ahn:
  versie: "AHN actueel (PDOK, stand 2026-11)"
  dtm_index: https://service.pdok.nl/rws/actueel-hoogtebestand-nederland/atom/downloads/dtm_05m/kaartbladindex.json
  dsm_index: https://service.pdok.nl/rws/actueel-hoogtebestand-nederland/atom/downloads/dsm_05m/kaartbladindex.json
  url_veld: url
  vorige: null        # optional {versie, dtm_index, dsm_index}; PDOK feed has no previous version
tuin: {delen: [voor], max_diepte_m: 25, voorzijde_zoekafstand_m: 30, min_oppervlak_m2: 5}
signalen:
  resolutie_m: 0.25
  exg_drempel: 0.05
  ndvi_drempel: 0.2   # spec says 0.3; PDOK JPEG CIR measured lower (crown median 0.27–0.33)
  schaduw_helderheid: 40
  s2_hoogte_m: 2.0
  s3_hoogte_m: 1.0
  s3_buffer_m: 1.5
  s4a_hoogte_m: 1.5
  s4a_min_m2: 2.0
  s4a_vorige_max_m: 1.0
  s4a_standaardberging_max_m2: 10
score:   # gewicht, onder, boven
  S1: {gewicht: 25, onder: 0.0, boven: 0.30}
  S2: {gewicht: 20, onder: 0.05, boven: 0.40}
  S3: {gewicht: 20, onder: 0.5, boven: 5.0}
  S4a: {gewicht: 25, onder: 0.0, boven: 10.0}
  S5: {gewicht: 10, onder: 0.5, boven: 1.0}
terugkoppeling:
  top_n: 20
  controlegroep_n: 0
  seed: 42
  oordeel: [terecht, onterecht]
  oorzaak: ["verwaarlozing", "overhang", "bouwwerk", "verharding", "rommel/grofvuil", "schaduw", "straatboom/openbaar groen", "tuinvlak verkeerd", "toegestaan (ZAV/vergunning)", "beeld verouderd", "overig"]
  actie: ["geen actie", "gesprek", "zaak geopend", "al bekend"]
```

- [ ] **Step 1: Write failing tests**

```python
def test_example_config_loads(): cfg = laad_config(Path("config/tuinscan.example.yaml")); assert cfg.score["S4a"].gewicht == 25
def test_weights_must_sum_100(tmp_path): # S1 gewicht 30 → ConfigFout mentioning "score" and "100"
def test_bbox_must_be_ordered(tmp_path): # xmin >= xmax → ConfigFout mentioning "gebied.bbox_rd"
def test_tuin_delen_only_voor_achter(tmp_path): # ["zij"] → ConfigFout
def test_hash_changes_with_weights_not_with_paths(): # change mappen.cache → same hash; change S1 gewicht → different
def test_onedrive_refused(tmp_path):
    env = {"OneDriveCommercial": str(tmp_path / "OneDrive - Cazas")}
    with pytest.raises(OneDriveFout): controleer_buiten_onedrive([tmp_path / "OneDrive - Cazas" / "data"], env)
def test_onedrive_ok_elsewhere(tmp_path): # same env, path tmp_path/"data" → no error
def test_foutenlijst_csv(tmp_path): # two entries → csv with header vhe_nummer,stap,reden and 2 rows
def test_cli_help_lists_steps(capsys): # main(["--help"]) exit 0; output contains download, tuinen, score, rapport, terugkoppeling, inspecteer
```

- [ ] **Step 2: Run** `uv run pytest tests/test_config.py tests/test_mappen.py tests/test_fouten.py -v` — Expected: FAIL (import errors).
- [ ] **Step 3: Implement.** Config as frozen dataclasses with a single `_valideer(cfg)`; OneDrive guard checks env vars `OneDrive`, `OneDriveCommercial`, `OneDriveConsumer` and any path part starting with `OneDrive`. CLI calls the guard for all three data folders before any subcommand. Subcommands not yet implemented print "nog niet beschikbaar" and return 2.
- [ ] **Step 4: Run** same command — Expected: PASS. Also `uv run ruff check .` clean.
- [ ] **Step 5: Commit** `feat: project skeleton, config, folder guard`

---

### Task 2: Tobias export import with unknown column names (`inspecteer`)

**Files:**
- Create: `src/tuinscan/tobias.py`, `src/tuinscan/inspecteer.py`
- Modify: `src/tuinscan/cli.py` (subcommand `inspecteer`)
- Test: `tests/test_tobias.py`, `tests/test_inspecteer.py`

**Interfaces:**
- Consumes: `Config.tobias`, `Mappen`, `Foutenlijst`.
- Produces:
  - Canonical fields `VERPLICHT = ("vhe_nummer", "bag_vbo_id", "wijk", "complex", "woningtype")`, address via either `adres` or `straat`+`huisnummer` (+ optional `huisletter`, `toevoeging`, `postcode`, `plaats`); `OPTIONEEL = ("eigen_tuin", "standaardberging", "status", "sloop_renovatie")`.
  - `lees_ruwe_export(pad: Path, blad: str | None, kopregel: int | None) -> pd.DataFrame` — all cells as `str` (`dtype=str` via openpyxl), header auto-detected in the first 15 rows (first row where ≥ 50 % of cells are non-numeric text and ≥ 3 cells filled).
  - `normaliseer_bag_id(waarde: object) -> str | None` — int/float/str → 16-digit zero-padded string; valid only if `re.fullmatch(r"\d{4}01\d{10}", s)`.
  - `stel_mapping_voor(df: pd.DataFrame) -> dict[str, MappingVoorstel]` with `MappingVoorstel(kolom: str | None, zekerheid: Literal["hoog","middel","laag"], reden: str)`.
  - `laad_mapping(pad: Path) -> dict[str, str]`; raises `ConfigFout` listing unmapped required fields and the export headers available.
  - `lees_vhes(cfg, mappen, fouten) -> pd.DataFrame` columns `vhe_nummer, bag_vbo_id, adres, wijk, complex, woningtype` (+ mapped optionals), filtered per `tobias.filters`; writes `ronde_dir/vhe.parquet`.
  - CLI `tuinscan inspecteer --config C` prints per export column: header, 3 sample values (truncated 30 chars), suggested field, zekerheid; writes `invoer/kolommen.yaml` **only if it does not exist** (otherwise `kolommen.voorstel.yaml`).

Suggestion rules: value patterns beat header names. `bag_vbo_id`: ≥ 80 % of non-empty values normalise to a valid id → hoog. `postcode`: ≥ 80 % match `^\d{4}\s?[A-Za-z]{2}$` → hoog. `huisnummer`: ≥ 80 % integers 1–99999 → middel. Header aliases (normalised: lowercase, alnum only) → middel: `vhe_nummer`: vhe, vhenummer, vhenr, eenheid, verhuureenheid, rentalobject, rentalobjectid, objectnummer; `wijk`: wijk, buurt, district, wijknaam; `complex`: complex, complexnummer, cluster, bouwblok; `woningtype`: woningtype, type, objecttype, woningsoort, typewoning; `eigen_tuin`: tuin, eigentuin, heefttuin; `standaardberging`: berging, standaardberging; `status`: status, verhuurstatus, leegstand; `sloop_renovatie`: sloop, renovatie, project. A column is suggested for at most one field.

- [ ] **Step 1: Write failing tests** (build xlsx fixtures with openpyxl in `tmp_path`)

```python
def test_bag_id_from_number(): assert normaliseer_bag_id(363010000123456) == "0363010000123456"
def test_bag_id_float_valid_or_none(): v = normaliseer_bag_id(363010000123456.0); assert v == "0363010000123456"
def test_bag_id_rejects_non_vbo(): assert normaliseer_bag_id("0363100012345678") is None  # type code 10 = pand
def test_header_detected_below_title(tmp_path): # row1 "Export Tobias 25-11-2026", row3 headers → columns from row 3
def test_suggest_by_pattern_with_odd_headers(tmp_path):
    # headers ["Kol A","Kol B","Kol C"], values B = BAG ids (as numbers), C = "1234 AB"
    v = stel_mapping_voor(df); assert v["bag_vbo_id"].kolom == "Kol B" and v["bag_vbo_id"].zekerheid == "hoog"; assert v["postcode"].kolom == "Kol C"
def test_suggest_by_alias(tmp_path): # header "Verhuureenheid" → vhe_nummer, middel
def test_missing_mapping_lists_headers(tmp_path): # kolommen.yaml without wijk → ConfigFout containing "wijk" and an export header
def test_invalid_bag_id_to_foutenlijst(tmp_path): # one row BAG "12345" → absent from result, fout reden contains "exporteer de kolom als tekst"
def test_duplicate_vhe_keeps_first(tmp_path): # two rows same VHE → 1 row kept, 1 fout "dubbel VHE-nummer"
def test_filters_applied_only_when_mapped(tmp_path): # filters.woningtype ["eengezinswoning"] keeps matching rows; status_uitsluiten set but status unmapped → no-op + warning logged
def test_address_composed(tmp_path): # straat "Lindelaan", huisnummer "12", toevoeging "A" → adres "Lindelaan 12A"
def test_inspecteer_does_not_overwrite(tmp_path): # existing kolommen.yaml unchanged, kolommen.voorstel.yaml written
```

- [ ] **Step 2: Run** `uv run pytest tests/test_tobias.py tests/test_inspecteer.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the signatures above.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat: Tobias import with configurable column mapping and inspecteer`

---

### Task 3: PDOK vector client (OGC API Features) + cache manifest

**Files:**
- Create: `src/tuinscan/pdok/{__init__,ogc,cache}.py`
- Test: `tests/test_ogc.py`, `tests/test_cache.py`

**Interfaces:**
- Produces:
  - `RD = "http://www.opengis.net/def/crs/EPSG/0/28992"`
  - `haal_features(basis_url: str, collectie: str, bbox: tuple[float,float,float,float], sessie: requests.Session, limit: int = 1000) -> gpd.GeoDataFrame` — GET `{basis_url}/collections/{collectie}/items` with `bbox`, `bbox-crs=RD`, `crs=RD`, `limit`, `f=json`; follows `links[rel=next]` until absent; keeps the GeoJSON feature `id` as column `feature_id`; result CRS EPSG:28992; empty frame with geometry column if no features. Retries 3× on 429/5xx with backoff 2/4/8 s.
  - `Manifest(pad: Path)`: `.heeft(sleutel: str, bbox) -> bool`, `.registreer(sleutel, url, bbox, extra: dict)`, `.lees(sleutel) -> dict`; JSON on disk, written atomically (temp file + replace).
  - `vector_pad(cache: Path, sleutel: str) -> Path` → `cache/vector/{sleutel}.gpkg`.

- [ ] **Step 1: Failing tests** (mock with `responses`)

```python
def test_follows_next_links(): # page1 2 features + next, page2 1 feature → 3 rows, crs 28992
def test_sends_bbox_and_crs_params(): # assert request query has bbox="1,2,3,4", bbox-crs=RD, crs=RD
def test_no_address_params(): # request query keys ⊆ {"bbox","bbox-crs","crs","limit","f"} on first call
def test_retry_on_503(): # 503 then 200 → result ok, 2 calls (patch sleep)
def test_empty_collection(): # 0 features → empty GeoDataFrame with crs 28992
def test_feature_id_kept(): # feature {"id": "uuid-1", ...} → row feature_id == "uuid-1"; list properties (pand.href) preserved as list
def test_manifest_roundtrip_and_bbox_mismatch(tmp_path): # registreer bbox A → heeft(A) True, heeft(B) False
```

- [ ] **Step 2: Run** `uv run pytest tests/test_ogc.py tests/test_cache.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat: PDOK OGC API client and cache manifest`

---

### Task 4: WMTS mosaic for the werkgebied

**Files:**
- Create: `src/tuinscan/pdok/wmts.py`
- Test: `tests/test_wmts.py` (capabilities fixture XML in `tests/data/wmts_caps.xml`, hand-written, 2 layers, matrices 14 and 15)

**Interfaces:**
- Produces:
  - `TileMatrix(id: str, resolutie: float, origin_x: float, origin_y: float, tegel_b: int, tegel_h: int, matrix_b: int, matrix_h: int)`; `resolutie = ScaleDenominator * 0.00028`.
  - `lees_capabilities(xml: bytes, matrixset: str) -> Capabilities(lagen: dict[str, str_format], matrices: dict[str, TileMatrix])`.
  - `tegels_voor_bbox(m: TileMatrix, bbox) -> list[tuple[int,int]]` (col,row): `col = floor((x - origin_x) / (res*tegel_b))`, `row = floor((origin_y - y) / (res*tegel_h))`, inclusive ranges clipped to matrix size.
  - `download_mozaiek(url: str, laag: str, matrixset: str, matrix: TileMatrix, formaat: str, bbox, doel: Path, sessie, workers: int) -> MozaiekInfo(pad, tegels_totaal, tegels_mislukt)` — REST GetTile `{url}/{laag}/{matrixset}/{matrix.id}/{col}/{row}.jpeg` (verified), decode with Pillow; a tile that is HTTP ≠ 200 **or uniformly white (255) or black (0)** counts as failed (PDOK returns white 200-tiles where a year has no coverage yet); write a 3-band uint8 tiled/deflate GeoTIFF covering exactly the tile union, nodata 0, plus an internal mask for failed tiles. Thread pool of `workers`.
  - `lijst_lagen(url) -> list[str]` (for Task 6).

- [ ] **Step 1: Failing tests**

```python
def test_resolution_from_scale(): # ScaleDenominator 750 → 0.21 m; origin (-285401.92, 903401.92) parsed
def test_tiles_cover_bbox(): # known matrix, bbox spanning 2×3 tiles → 6 (col,row) pairs, exact values
def test_mosaic_georeference(tmp_path): # mock 4 tiles of solid colours → GeoTIFF bounds == tile-union bounds, crs 28992, pixel at tile 2 centre == its colour
def test_failed_tile_is_masked(tmp_path): # one tile 404 → tegels_mislukt == 1, that area read_masks()==0, no exception
def test_white_tile_is_masked(tmp_path): # one tile all-255 JPEG with HTTP 200 → tegels_mislukt == 1, masked
```

- [ ] **Step 2: Run** `uv run pytest tests/test_wmts.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat: WMTS tile mosaic to GeoTIFF`

---

### Task 5: AHN COG reader + `download` step

**Files:**
- Create: `src/tuinscan/pdok/ahn.py`, `src/tuinscan/download.py`
- Modify: `src/tuinscan/cli.py` (`download [--vernieuw] [--lijst-lagen]`)
- Test: `tests/test_ahn.py`, `tests/test_download.py`

**Interfaces:**
- Consumes: Tasks 3–4.
- Produces:
  - `kaartbladen_voor_bbox(index: gpd.GeoDataFrame, url_veld: str, bbox) -> list[str]`.
  - `lees_ahn(bronnen: list[str], bbox, doel: Path) -> Path` — opens each via `rasterio.open(bron)` (URLs prefixed `/vsicurl/`), windowed read of the bbox, `rasterio.merge.merge`, float32 GeoTIFF, nodata preserved. Before opening remote files set `GDAL_HTTP_PROXY` from `HTTPS_PROXY` and `CURL_CA_BUNDLE`/`GDAL_CURL_CA_BUNDLE` from `REQUESTS_CA_BUNDLE` when present (corporate proxy).
  - `voer_download_uit(cfg, mappen, vernieuw: bool) -> DownloadRapport` — bbox = `gebied.bbox_rd` grown by `buffer_m`; fetches vector keys `bag_pand, bag_vbo, brk_perceel, bgt_wegdeel, bgt_begroeid`, raster keys `zomer_nu, zomer_vorig, cir_nu, cir_vorig, winter_nu, winter_vorig` (a layer with `laag: null` is skipped and recorded as `niet_geconfigureerd`), `ahn_dtm, ahn_dsm` (+ `ahn_vorig_dtm/dsm` if `ahn.vorige`). Skips keys whose manifest bbox matches unless `vernieuw`. Raster paths `cache/raster/{sleutel}.tif`.
  - `--lijst-lagen` prints `lijst_lagen` for both WMTS services and exits.

- [ ] **Step 1: Failing tests** (AHN: local GeoTIFF files as "remote" sources; download: monkeypatch `haal_features`, `download_mozaiek`, `lees_ahn`)

```python
def test_kaartbladen_selected_by_intersection(): # 3 index tiles, bbox touches 2 → 2 urls
def test_ahn_merge_window(tmp_path): # two adjacent local tifs, bbox across seam → output bounds == bbox, values from both
def test_download_skips_cached(tmp_path): # second run calls nothing
def test_download_vernieuw_refetches(tmp_path)
def test_unconfigured_layer_skipped(tmp_path): # zomer_vorig.laag None → rapport.niet_geconfigureerd contains "zomer_vorig"
def test_bbox_is_only_spatial_input(tmp_path): # all monkeypatched calls receive the same grown bbox; none receives a point/address
```

- [ ] **Step 2: Run** `uv run pytest tests/test_ahn.py tests/test_download.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat: AHN COG reader and download step`

---

### Task 6: [laptop] Check PDOK behind the corporate proxy

PDOK sources, layer names, fields and the AHN COG read were verified from the cloud environment on 2026-11-25 (`docs/pdok-bronnen.md`). What is left can only be checked on the work laptop.

**Files:**
- Modify: `docs/pdok-bronnen.md` (section "Werklaptop")

- [ ] **Step 1:** IT check: uv/Python allowed, PyPI, `api.pdok.nl` and `service.pdok.nl` reachable through the proxy. Install:

```bash
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
uv sync
```

- [ ] **Step 2:** `/vsicurl/` behind the proxy (GDAL uses its own HTTP stack):

```bash
uv run python -c "import rasterio; print(rasterio.open('/vsicurl/https://service.pdok.nl/rws/ahn/atom/downloads/dsm_05m/R_25GN1.tif').profile)"
```

Expected: a profile with `crs: EPSG:28992`. If it fails, retry with `GDAL_HTTP_PROXY` and `GDAL_CURL_CA_BUNDLE` set (Task 5 sets these from `HTTPS_PROXY`/`REQUESTS_CA_BUNDLE`). Still failing → record it and stop: fallback (one PDOK AHN WCS request for the whole bbox) needs a decision.
- [ ] **Step 3:** Set the real wijk `gebied.bbox_rd`, run `uv run tuinscan download --config config/proefwijk.yaml`, open GeoPackages and GeoTIFFs in QGIS and check alignment of luchtfoto, AHN and BAG; confirm the summer layers have coverage (no white areas) for the wijk.
- [ ] **Step 4: Commit** `docs: PDOK check on work laptop`

---

### Task 7: Koppeling VHE → verblijfsobject → pand → perceel

**Files:**
- Create: `src/tuinscan/koppeling.py`
- Test: `tests/test_koppeling.py`; `tests/conftest.py` (fixture `miniwijk`)

**Interfaces:**
- Consumes: `lees_vhes` output, cached vectors, `Config.pdok.velden`, `Foutenlijst`.
- Produces:
  - Fixture `miniwijk(tmp_path) -> MiniWijk` with: road strip (BGT wegdeel `functie=voetpad` along y=0..2, and a `rijbaan` south of it), perceel P1 (row of 3 houses as **one** BAG pand with 3 vbo's, one perceel), perceel P2 (detached house, own pand + a BAG shed pand), perceel P3 (empty), BGT begroeid openbaar groen east of P2. Vbo ids valid 16-digit. Also writes the matching GeoPackages into a cache dir and a matching manifest. Reused by Tasks 8–15.
  - `koppel(vhes: pd.DataFrame, vbo: gpd.GeoDataFrame, panden: gpd.GeoDataFrame, percelen: gpd.GeoDataFrame, velden, fouten) -> gpd.GeoDataFrame` — one row per VHE: `vhe_nummer, bag_vbo_id, pand_id, perceel_id, geometry (vbo point)` + VHE columns. Pand = the pand whose `feature_id` equals the last URL segment of an entry in the vbo's `pand.href` list (if several, the one containing the point; fallback when the href list is empty: point-in-polygon). Perceel = the perceel containing the vbo point (fallback: largest overlap with the pand). The `miniwijk` fixture uses this real PDOK shape (feature ids + `pand.href` URLs).
  - Fouten (stap `koppeling`): "BAG-verblijfsobject niet gevonden in werkgebied" (hint about Excel rounding if the id ends in `0`), "geen pand", "geen perceel", "meerdere VHE's op hetzelfde verblijfsobject" (all but first).
  - `koppelpercentage(n_vhe: int, n_gekoppeld: int) -> float`.

- [ ] **Step 1: Failing tests**

```python
def test_all_miniwijk_vhes_link(miniwijk): # 4 VHEs → 4 rows, P1 houses share perceel_id
def test_unknown_vbo_to_foutenlijst(miniwijk): # extra VHE with unknown id → 4 rows, 1 fout stap "koppeling"
def test_two_vhes_one_vbo(miniwijk): # duplicate vbo → 1 row kept, fout "meerdere VHE's"
def test_pand_via_href(miniwijk): # vbo with pand.href [".../items/<uuid>"] links to pand with feature_id <uuid>
def test_pand_fallback_spatial(miniwijk): # empty pand.href → pand found by point-in-polygon
```

- [ ] **Step 2: Run** `uv run pytest tests/test_koppeling.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat: link VHE to BAG pand and perceel`

---

### Task 8: Tuinvlak (voor/achter, complex split, QGIS corrections) + `tuinen` step

**Files:**
- Create: `src/tuinscan/tuinvlak.py`, `src/tuinscan/stappen.py` (`stap_tuinen`)
- Modify: `src/tuinscan/cli.py`
- Test: `tests/test_tuinvlak.py`

**Interfaces:**
- Consumes: `koppel` output, cached `bag_pand`, `bag_vbo`, `brk_perceel`, `bgt_wegdeel`; `Config.tuin`, `Config.pdok`.
- Produces:
  - `woon_zaden(perceel, vbo_woon, panden) -> gpd.GeoDataFrame` — one seed per woon-vbo on the perceel (all woon-vbo's, not only our VHEs): the pand centroid if that pand holds exactly one woon-vbo, else the vbo point.
  - `splits_perceel(perceel_geom, zaden, panden, max_diepte_m) -> dict[str, Polygon]` keyed by vbo id: Voronoi (`shapely.voronoi_polygons`) of the seeds clipped to the perceel, minus all BAG panden, intersected with `buffer(own pand or vbo point, max_diepte_m)`. One seed → perceel − panden ∩ buffer. *Deviation from the spec wording "loodrecht op de pandscheidingen": BAG often stores a whole row as one pand, so party walls are not in the data; the bisector between adjacent house seeds gives the same cut for regular rows and still works for one-pand rows. Correction in QGIS remains the fallback.*
  - `deel_voor_achter(tuin, pand_geom, vbo_punt, wegdelen, zoekafstand_m) -> list[tuple[Literal["voor","achter"], Polygon]]` — front vector = from pand centroid to the nearest point of the nearest wegdeel (any functie, incl. voetpad) within `zoekafstand_m` of the vbo point; explode tuin parts; part is `voor` if `dot(part.centroid − pand.centroid, front) > 0`, else `achter`; parts of the same kind are unioned. No wegdeel found → everything `achter` + fout "voorzijde niet bepaald".
  - `pas_correcties_toe(tuinen: gpd.GeoDataFrame, correcties_pad: Path) -> gpd.GeoDataFrame` — if `invoer/tuincorrecties.gpkg` (layer `tuinvlak`, columns `vhe_nummer, deel`) exists, its geometries replace the automatic ones per `(vhe_nummer, deel)`; rows marked `methode="handmatig"`. Corrections for VHEs not in this ronde are ignored with a log warning.
  - `stap_tuinen(cfg, mappen) -> TuinenRapport(n_vhe, n_tuin, percentage)` — runs `lees_vhes` → `koppel` → split → voor/achter → correcties → keeps only `cfg.tuin.delen` → drops parts `< min_oppervlak_m2` (fout "tuinvlak te klein") → writes `ronde.json` (F03: datum, gebied, filters, kolommen-mapping, parameterset) and `tuinen.gpkg` layer `tuinvlak` (`vhe_nummer, deel, methode ∈ {perceel, gesplitst, handmatig}, oppervlak_m2, adres, wijk, complex, geometry`) and `foutenlijst.csv`; prints "Koppeling + tuinvlak gelukt voor X van Y VHE's (Z %)" and warns when Z < 90.

- [ ] **Step 1: Failing tests** (on `miniwijk`)

```python
def test_detached_tuin_excludes_house_and_shed(miniwijk): # P2 tuin ∩ panden area == 0; area == perceel − both panden
def test_row_split_three_cells(miniwijk): # P1 → 3 tuinen, pairwise intersection area < 0.01, union ≈ perceel − pand
def test_row_cut_perpendicular_to_row(miniwijk): # boundary between house 1 and 2 is a line with |dx| < 0.01 (row along x)
def test_front_is_road_side(miniwijk): # road south → voor part centroid.y < pand centroid.y
def test_no_road_all_achter(miniwijk): # wegdelen empty → only "achter" + fout "voorzijde niet bepaald"
def test_delen_voor_only(miniwijk): # cfg.tuin.delen ["voor"] → no "achter" rows
def test_correction_replaces_geometry(miniwijk, tmp_path): # correction for (vhe, "voor") → geometry equal, methode "handmatig"
def test_max_depth(miniwijk): # very deep P2 → tuin within 25 m buffer of pand
def test_stap_tuinen_percentage(miniwijk, capsys): # 4/4 → "100 %" in output; gpkg readable with pyogrio; ronde.json has parameterset
```

- [ ] **Step 2: Run** `uv run pytest tests/test_tuinvlak.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat: garden polygons with front/back split and QGIS corrections`

---

### Task 9: Raster windows on a common grid + vegetation mask

**Files:**
- Create: `src/tuinscan/rasters.py`; extend `tests/conftest.py` (synthetic GeoTIFFs for `miniwijk`)
- Test: `tests/test_rasters.py`

**Interfaces:**
- Consumes: cache raster paths from Task 5.
- Produces:
  - `Raster = np.ma.MaskedArray` (bands, h, w); masked = nodata / failed tile / outside source.
  - `lees_venster(pad: Path | None, bounds, resolutie: float, resampling: Resampling) -> Raster | None` — `None` if `pad` is `None` or missing; reprojects (`rasterio.warp.reproject`) onto the grid with origin `floor(bounds/res)*res`.
  - `TuinRasters` dataclass: `transform, vorm, tuin_masker (bool), zone_masker (bool, S3 overhang zone), zomer_nu, zomer_vorig, cir_nu, cir_vorig, ndsm, ndsm_vorig` (each `Raster | None`).
  - `bouw_tuinrasters(tuin: Polygon, zone: Polygon | None, bronnen: dict[str, Path | None], res: float) -> TuinRasters` — `bronnen` keys are the Task 5 cache keys (`zomer_nu, zomer_vorig, cir_nu, cir_vorig, ahn_dtm, ahn_dsm, ahn_vorig_dtm, ahn_vorig_dsm`) — window = bounds of `tuin ∪ zone` + 1 m; RGB/CIR bilinear, AHN nearest; `ndsm = dsm − dtm` with DTM gaps filled by `rasterio.fill.fillnodata(max_search_distance=40 px)` (measured: DTM has large gaps under buildings).
  - `overhang_zone(tuin, panden, bgt_voetpad, bgt_groen, buffer_m) -> Polygon | None` = `(tuin.buffer(buffer_m) − tuin − panden) ∩ (voetpad ∪ openbaar groen)`.

- [ ] **Step 1: Failing tests**

```python
def test_window_aligned_to_grid(miniwijk): # transform.c % 0.25 == 0
def test_missing_source_is_none(miniwijk): # bronnen["zomer_vorig"] None → tr.zomer_vorig is None
def test_outside_coverage_masked(miniwijk): # tuin half outside the tif → ≈ half of tuin pixels masked
def test_ndsm_fills_dtm_gap(miniwijk): # DTM hole of 30 px → ndsm finite there
def test_overhang_zone_only_public(miniwijk): # zone ⊆ voetpad ∪ groen; zone ∩ tuin area == 0
```

- [ ] **Step 2–4:** `uv run pytest tests/test_rasters.py -v` FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: per-garden raster windows on common grid`

---

### Task 10: Signals S1, S2, S3, S4a, S5

**Files:**
- Create: `src/tuinscan/signalen.py`
- Test: `tests/test_signalen.py` (pure numpy arrays, no files)

**Interfaces:**
- Consumes: `TuinRasters`, `Config.signalen`.
- Produces (pixel area `a = res²`; "geldig" = in mask, unmasked in every input used, not shadow):
  - `exg(rgb) -> np.ndarray` — chromatic `r,g,b = R/S, G/S, B/S` (S = R+G+B, S=0 → 0); `2g − r − b`.
  - `ndvi_cir(cir) -> np.ndarray` — band1 = NIR, band2 = rood: `(b1 − b2)/(b1 + b2)`.
  - `schaduw(rgb, helderheid) -> bool` — mean(R,G,B) < `schaduw_helderheid`.
  - `vegetatie(zomer, cir, cfg) -> bool` — `ndvi_cir(cir) > ndvi_drempel` OR `exg(zomer) > exg_drempel`; `cir` is the **summer** CIR of the same year (NDVI part skipped if `cir` is None).
  - `s1_groentoename(tr, cfg) -> float | None` = groen-aandeel(zomer_nu) − groen-aandeel(zomer_vorig) over geldige tuin pixels (vegetation per year with that year's CIR). `None` if either year missing.
  - `s2_hoge_begroeiing(tr, cfg) -> float | None` = share of geldige tuin pixels with vegetatie_nu AND ndsm > `s2_hoogte_m`.
  - `s3_overhang(tr, cfg) -> float | None` = m² in `zone_masker` with vegetatie_nu AND ndsm > `s3_hoogte_m`; `0.0` if zone is empty; `None` if ndsm missing.
  - `s4a_hoog_object(tr, cfg, negeer_berging: bool) -> float | None` = total m² of connected regions (`rasterio.features.shapes`, 8-connected) in the tuin with ndsm > `s4a_hoogte_m` AND NOT vegetatie_nu, each ≥ `s4a_min_m2`; if `ndsm_vorig` present, a region counts only if its mean `ndsm_vorig` < `s4a_vorige_max_m`; if `negeer_berging`, the largest region ≤ `s4a_standaardberging_max_m2` is ignored.
  - `s5_verharding(tr, cfg) -> tuple[float | None, float | None]` = (share of geldige tuin pixels non-vegetation in **both** summers, toename = non-veg share nu − vorig). If vorig missing: (None, None).
  - `bereken_signalen(tr, cfg, negeer_berging: bool) -> dict[str, float | None]` (caller passes `negeer_berging = VHE has standaardberging AND no BAG bijgebouw pand on its perceel`; the ZAV check is out of the PoC because the PoC export has no ZAV field) keys `S1, S2, S3, S4a, S5, S5_toename`. Any exception inside one signal → that key `None` + logged (never raises).

- [ ] **Step 1: Failing tests**

```python
def test_exg_green_vs_grey(): # pure green px > 0.5, grey (100,100,100) == 0
def test_ndvi_band_order(): # NIR 200, red 50 → 0.6
def test_s1_increase(): # 20 % green → 50 % green → 0.30 ± 0.01
def test_s1_none_without_previous_year()
def test_s2_share_over_2m(): # quarter of tuin green & 3 m → 0.25
def test_s3_counts_only_zone(): # tall green inside tuin not counted; 8 px tall green in zone at res 0.25 → 0.5 m²
def test_s4a_small_object_ignored(): # 1.5 m² grey 2 m block → 0.0; 4 m² block → 4.0
def test_s4a_existing_in_previous_ahn_ignored(): # same block in ndsm_vorig → 0.0
def test_s4a_standaardberging_ignored(): # one 6 m² block, negeer_berging=True → 0.0
def test_s5_both_years(): # 80 % grey both years → 0.8; toename 0.1 when vorig 70 %
def test_shadow_excluded_from_denominator(): # half tuin black → shares computed over other half
def test_all_masked_returns_none(): # tuin fully masked → S1, S2, S5 None (no ZeroDivisionError)
```

- [ ] **Step 2–4:** `uv run pytest tests/test_signalen.py -v` FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: signals S1-S5 from aerial imagery and AHN`

---

### Task 11: Score, redenen, provenance + `score` step

**Files:**
- Create: `src/tuinscan/score.py`; add `stap_score` to `src/tuinscan/stappen.py`
- Modify: `src/tuinscan/cli.py`
- Test: `tests/test_score.py`

**Interfaces:**
- Consumes: `tuinen.gpkg`, cache rasters, Tasks 9–10, `parameterset_hash`.
- Produces:
  - `schaal(x: float, onder: float, boven: float) -> float` = `min(1, max(0, (x − onder)/(boven − onder)))`.
  - `ScoreUitkomst(score: float, bijdragen: dict[str, float], redenen: list[str], reden_codes: list[str], ontbrekend: list[str])`.
  - `bereken_score(signalen: dict[str, float | None], cfg) -> ScoreUitkomst` — `score = Σ gewicht_i · schaal(x_i)`, rounded to 1 decimal; `None` signal contributes 0 and is listed in `ontbrekend`; `redenen` = the two largest contributions > 0, text from `REDEN_TEKST`:

```python
REDEN_TEKST = {
  "S1": "Groentoename: +{v:.0%} groen t.o.v. vorige zomer",
  "S2": "Hoge begroeiing: {v:.0%} van de tuin hoger dan {h} m",
  "S3": "Overhang: {v:.1f} m² begroeiing boven stoep of openbaar groen",
  "S4a": "Hoog object: {v:.1f} m² niet in BAG",
  "S5": "Verharding: {v:.0%} blijvend niet-groen",
}
```

  - Per VHE the score uses the **sum of parts** for area signals (S3, S4a) and the **area-weighted mean** for share signals (S1, S2, S5) over the VHE's tuin parts. Signals are stored per part and per VHE.
  - `stap_score(cfg, mappen) -> pd.DataFrame` — writes `signalen.parquet` (per part), `scores.parquet` (per VHE: `vhe_nummer, adres, wijk, complex, score, rang_in_wijk, S1..S5, S5_toename, bijdrage_*, reden_1, reden_2, reden_codes, ontbrekend, zomer_jaar, zomer_vorig_jaar, winter_jaar, ahn_versie, parameterset`) and layer `scores` (tuin geometry + score) in `tuinen.gpkg`. Per-VHE exceptions → foutenlijst stap `score`. Always recomputes signals (cheap; avoids stale results after config changes).

- [ ] **Step 1: Failing tests**

```python
def test_schaal_clips(): assert schaal(-1,0,1)==0 and schaal(2,0,1)==1 and schaal(0.5,0,1)==0.5
def test_score_max_is_100(): # all signals at boven → 100.0
def test_missing_signal_counts_zero(): # S1 None, others at boven → 75.0, ontbrekend ["S1"]
def test_two_heaviest_reasons(): # contributions S3 20, S4a 12.5, S5 2 → reden_codes ["S3","S4a"]
def test_no_reasons_when_all_zero(): # score 0 → redenen []
def test_fixed_scale_not_per_wijk(miniwijk): # adding a second wijk with higher values does not change wijk 1 scores
def test_provenance_columns(miniwijk): # scores.parquet has jaar/versie columns from config + parameterset == parameterset_hash(cfg)
def test_one_bad_vhe_does_not_stop(miniwijk, monkeypatch): # bereken_signalen raises for one VHE → others scored, fout stap "score"
```

- [ ] **Step 2–4:** `uv run pytest tests/test_score.py -v` FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: fixed 0-100 score with reasons and provenance`

---

### Task 12: Uitsneden (thumbnails + detail images)

**Files:**
- Create: `src/tuinscan/uitsneden.py`
- Test: `tests/test_uitsneden.py`

**Interfaces:**
- Consumes: `tuinen.gpkg`, cache rasters.
- Produces:
  - `maak_uitsnede(bron: Path | None, tuin_geom, buffer_m: float, max_px: int, doel: Path) -> Path | None` — RGB crop of `bounds + buffer_m` (5 m), resized to longest side `max_px`, tuin outline drawn (2 px, yellow `#FFD400`), JPEG quality 85. `None` if source missing or crop fully masked.
  - `maak_uitsneden(vhe_nummer, tuin_geom, bronnen, map: Path) -> dict[str, Path | None]` keys `thumb_zomer_nu, thumb_zomer_vorig` (200 px) and `detail_winter_nu, detail_winter_vorig, detail_zomer_nu, detail_zomer_vorig` (800 px). Files `uitsneden/{vhe}_{key}.jpg`.

- [ ] **Step 1: Failing tests**

```python
def test_thumb_max_200px(miniwijk, tmp_path): # max(img.size) == 200
def test_outline_drawn(miniwijk, tmp_path): # some pixels ≈ (255,212,0)
def test_missing_source_returns_none(miniwijk, tmp_path)
```

- [ ] **Step 2–4:** `uv run pytest tests/test_uitsneden.py -v` FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: garden thumbnails and detail images`

---

### Task 13: Offline HTML report + `rapport` step

**Files:**
- Create: `src/tuinscan/rapport.py`, `src/tuinscan/templates/rapport.html.j2`; add `stap_rapport` to `stappen.py`
- Modify: `src/tuinscan/cli.py`
- Test: `tests/test_rapport.py`

**Interfaces:**
- Consumes: `scores.parquet`, `tuinen.gpkg`, `foutenlijst.csv`, Task 12.
- Produces:
  - `stap_rapport(cfg, mappen) -> Path` — makes uitsneden, renders `rapport.html`: one self-contained file (inline CSS/JS, thumbnails as base64 `data:` URIs, **no external URLs**); detail images linked relatively (`uitsneden/...`) and shown in an in-page overlay. Content: banner "Signaallijst – de wijkbeheerder beslist altijd zelf ter plekke. Niet mailen of delen."; ronde, provenance (years, AHN version, parameterset), counts (VHE's, tuinen, fouten); wijk selector; table per wijk sorted by score desc: rang, adres, VHE, complex, score, reden 1, reden 2, thumb nu, thumb vorig, "ontbrekend" badge, detail link. Light/dark via `prefers-color-scheme`. Printable.

- [ ] **Step 1: Failing tests**

```python
def test_sorted_per_wijk(miniwijk): # rows of wijk A appear in descending score
def test_no_external_urls(miniwijk): # no "http://" or "https://" in rapport.html
def test_thumbnails_embedded(miniwijk): # "data:image/jpeg;base64," count == 2 × n_vhe (minus missing)
def test_missing_signal_badge(miniwijk): # VHE with ontbrekend ["S1"] shows "S1 ontbreekt"
def test_html_escapes_address(miniwijk): # adres "<b>x</b>" rendered escaped
```

- [ ] **Step 2–4:** `uv run pytest tests/test_rapport.py -v` FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: offline HTML report`

---

### Task 14: Feedback Excel, read-back and kwaliteit + `terugkoppeling` step

**Files:**
- Create: `src/tuinscan/terugkoppeling.py`
- Modify: `src/tuinscan/cli.py` (`terugkoppeling maak` / `terugkoppeling lees`), `stappen.py`
- Test: `tests/test_terugkoppeling.py`

**Interfaces:**
- Consumes: `scores.parquet`, `Config.terugkoppeling`.
- Produces:
  - `selecteer_bezoeklijst(scores, top_n, controle_n, seed) -> pd.DataFrame` — top `top_n` per wijk (`groep="top"`) + `controle_n` random from the rest (`groep="controle"`, `numpy.random.default_rng(seed)`); top rows sorted by rang, controle rows after them.
  - `maak_terugkoppelbestand(lijst, cfg, pad: Path) -> Path` — sheet `Terugkoppeling`: `Groep, Rang, VHE, Adres, Wijk, Score, Reden 1, Reden 2, Redencodes, Bezoekdatum, Oordeel, Oorzaak, Actie`; VHE written as text (`number_format="@"`); sheet `Lijsten` with the three lists; `DataValidation(type="list")` on Oordeel/Oorzaak/Actie for 1000 rows, `showErrorMessage=True`; Bezoekdatum date validation. No free-text column.
  - `lees_terugkoppeling(pad, cfg) -> tuple[pd.DataFrame, list[str]]` — keyed on VHE (normalised to str, strip, numbers → int str); rows without Oordeel ignored; values compared case/space-insensitive to the lists; invalid → message "rij {n}: '{waarde}' is geen geldige {veld}" and row skipped.
  - `kwaliteit(scores, terug) -> pd.DataFrame` rows: one per signal code (`n_beoordeeld` = assessed VHEs where the code is in `reden_codes`, `n_terecht`, `precisie`), plus `top_n` and `controle` rows (share terecht). Writes `kwaliteit.csv`; prints table and "Top-{n}: {k} van {m} terecht — succescriterium ≥ 50 %: GEHAALD/NIET GEHAALD".

- [ ] **Step 1: Failing tests**

```python
def test_dropdowns_present(tmp_path): # openpyxl load → 3 list validations, ranges on Oordeel/Oorzaak/Actie
def test_no_free_text_column(tmp_path): # headers == exact list above
def test_vhe_kept_as_text(tmp_path): # VHE "000123" round-trips as "000123"
def test_read_resorted_and_blank_rows(tmp_path): # reorder rows, insert blank row → matched by VHE, blank ignored
def test_invalid_value_reported(tmp_path): # Oorzaak "kapotte schutting" → 1 message with row number, other rows kept
def test_precision_per_signal(): # 4 assessed with S3 in codes, 3 terecht → precisie 0.75
def test_top20_criterion_text(capsys): # 10/20 terecht → "GEHAALD"
def test_control_group_deterministic(): # same seed → same VHEs; none overlap with top
```

- [ ] **Step 2–4:** `uv run pytest tests/test_terugkoppeling.py -v` FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: structured feedback Excel and precision per signal`

---

### Task 15: `voorbeeld`, `alles`, `opruimen`, end-to-end test, README

**Files:**
- Create: `src/tuinscan/voorbeeld.py`, `README.md`, `tests/test_keten.py`, `tests/test_voorbeeld.py`
- Modify: `src/tuinscan/stappen.py`, `src/tuinscan/cli.py`

**Interfaces:**
- Produces:
  - `maak_voorbeeld_export(cfg, mappen, n: int = 200, seed: int = 1) -> Path` — from cached BAG: woon-vbo's (`gebruiksdoel` contains `woonfunctie`) that look grondgebonden (no other woon-vbo within 1.0 m and pand area / `aantal_verblijfsobjecten` ≥ 30 m²); address columns from the vbo fields (`openbare_ruimte_naam`, `huisnummer`, `huisletter`, `toevoeging`, `postcode`, `woonplaats_naam`); random sample `n`; writes `invoer/voorbeeld_export.xlsx` with deliberately non-canonical headers (`Eenheid`, `BAG VBO`, `Straatnaam`, `Huisnr`, `Postcode`, `Wijknaam`, `Complexnr`, `Type`) and BAG ids **as numbers** (exercises Task 2). VHE numbers synthetic (`VB00001`…), wijk = `gebied.naam`, complex = perceel id, type `eengezinswoning`. Lets the whole chain run before the real Tobias export exists.
  - `stap_alles(cfg, mappen)` = download → tuinen → score → rapport → `terugkoppeling maak`; prints duration per step and total (succescriterium < 1 uur).
  - `stap_opruimen(cfg, mappen, bevestiging: str)` — deletes `uitvoer/<ronde>`, the cache and the Tobias export in `invoer`; requires typing the ronde name; keeps `kolommen.yaml`, `tuincorrecties.gpkg` only with `--bewaar-correcties`.
  - README (Dutch): install without admin, folder setup outside OneDrive, first-time flow (`inspecteer` → edit `kolommen.yaml` → `download` → `tuinen` → check in QGIS → `score` → `rapport` → `terugkoppeling maak/lees` → `opruimen`), privacy rules from the spec's PoC *Voorwaarden*.

- [ ] **Step 1: Failing tests**

```python
def test_voorbeeld_roundtrip(miniwijk): # maak_voorbeeld_export → inspecteer suggests bag_vbo_id="BAG VBO" (hoog) → lees_vhes yields n rows
def test_keten_end_to_end(miniwijk): # cached miniwijk + export → stap_tuinen, stap_score, stap_rapport, terugkoppeling maak; all output files exist; scores.parquet has 4 rows
def test_opruimen_requires_name(miniwijk): # wrong name → nothing deleted; right name → ronde dir and cache gone
```

- [ ] **Step 2–4:** `uv run pytest -v` FAIL → implement → full suite PASS; `uv run ruff check .` clean.
- [ ] **Step 5: Commit** `feat: sample export, full chain, cleanup and README`

---

### Task 16: [laptop] PoC run in the wijk and success criteria

- [ ] **Step 1:** Before real addresses: FG informed (spec *Voorwaarden*), BitLocker on, data folder outside OneDrive.
- [ ] **Step 2:** Dry run with `voorbeeld`: `uv run tuinscan voorbeeld --config config/proefwijk.yaml` then `uv run tuinscan alles --config config/proefwijk.yaml`. Check report and QGIS layers.
- [ ] **Step 3:** When the Tobias export arrives: put it in `invoer/`, `uv run tuinscan inspecteer --config config/proefwijk.yaml`, review `kolommen.yaml`, run `alles`. Record in `docs/pdok-bronnen.md`-style note `docs/tobias-export.md` the confirmed header names (no data).
- [ ] **Step 4:** Check criteria: chain < 1 hour; koppeling + tuinvlak ≥ 90 %; correct bad tuinvlakken in QGIS (`tuincorrecties.gpkg`) and re-run `tuinen`/`score`/`rapport`.
- [ ] **Step 5:** Wijkbeheerder assesses top-20 on the laptop, fills `terugkoppeling.xlsx`; `uv run tuinscan terugkoppeling lees --config config/proefwijk.yaml` → ≥ 50 % terecht?
- [ ] **Step 6:** Product owner go/no-go for fase 0; then `uv run tuinscan opruimen --config config/proefwijk.yaml`.

---

## Local commands

```bash
uv sync
```

```bash
uv run pytest -v
```

```bash
uv run ruff check .
```

```bash
uv run tuinscan --help
```
