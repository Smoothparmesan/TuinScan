"""Synthetische mini-wijk voor tests (Tasks 7-15).

Lokale coördinaten (meter) rond OORSPRONG in EPSG:28992; x loopt langs de straat,
y = 0 is de rand voetpad/perceel, noord = achterkant.

    y=32 +--------P1---------+-------P2-------+groen+-----P3------+
         |   achtertuin      |   [schuur]     |     |   (leeg)    |
    y=18 |  +--R1--R2--R3--+ |  +--huis--+    |     |             |
         |  |  .   .   .   | |  |   .    |    |     |             |
    y= 8 |  +--------------+ |  +--------+    |     |             |
         |   voortuin        |   voortuin     |     |             |
    y= 2 +-------------------+----------------+-----+-------------+
         voetpad (y 0..2)
         rijbaan lokale weg (y -6..0)

Rasters (Task 9; zie RASTER_* hieronder): luchtfoto's als WMTS-mozaïek (3 x uint8,
0,21 m; winter 0,105 m; pixelraster vanaf de WMTS-oorsprong, interne mask) en AHN
(float32, 0,5 m, nodata float32-max; DTM nodata onder de panden).
    verharding (grijs) overal, daken op de panden, gras op het openbare groen,
    GROENVLAK in de achtertuin van R1 (1 m hoog; vorige zomer alleen de onderste helft),
    HAAG langs de voetpadrand van de voortuin van R2 (2,5 m hoog, 1 m over het voetpad),
    BLOK in de achtertuin van P2 (2 m hoog, 4 m², donkergrijs; vorige zomer afwezig).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import yaml
from openpyxl import Workbook
from rasterio.transform import from_origin
from shapely.geometry import MultiPolygon, Point, box

from tuinscan.config import Config, laad_config
from tuinscan.download import (
    LUCHTFOTO_SLEUTELS,
    VECTOR_SLEUTELS,
    _grow,
    lees_vector,
    manifest_pad,
    raster_pad,
    schrijf_vector,
)
from tuinscan.fouten import Foutenlijst
from tuinscan.mappen import Mappen
from tuinscan.pdok.cache import Manifest, vector_pad
from tuinscan.tobias import lees_vhes

VOORBEELD = Path(__file__).parent.parent / "config" / "tuinscan.example.yaml"
OORSPRONG = (120000.0, 485000.0)
BBOX_REL = (-5.0, -6.0, 57.0, 35.0)  # gebied.bbox_rd t.o.v. OORSPRONG
BUFFER_M = 5
PAND_ITEMS = "https://api.pdok.nl/kadaster/bag/ogc/v2/collections/pand/items/"

# relatieve rechthoeken (xmin, ymin, xmax, ymax)
PERCELEN = {"P1": (0, 2, 18, 32), "P2": (18, 2, 34, 32), "P3": (38, 2, 52, 32)}
PANDEN = {"rij": (0, 8, 18, 18), "vrijstaand": (21, 8, 29, 18), "schuur": (29, 26, 32, 29)}
VOETPAD = (-5, 0, 57, 2)
RIJBAAN = (-5, -6, 57, 0)
GROEN = (34, 2, 38, 32)
# huis -> (vbo-punt, pand, huisnummer, woningtype, complex)
HUIZEN = {
    "R1": ((3, 13), "rij", 1, "rijwoning", "C1"),
    "R2": ((9, 13), "rij", 3, "rijwoning", "C1"),
    "R3": ((15, 13), "rij", 5, "rijwoning", "C1"),
    "V": ((25, 13), "vrijstaand", 7, "vrijstaand", "C2"),
}

PERCEEL_IDS = {"P1": "100010001", "P2": "100010002", "P3": "100010003"}
PAND_IDS = {"rij": "0363100000000101", "vrijstaand": "0363100000000201",
            "schuur": "0363100000000202"}  # fmt: skip
PAND_FEATURE_IDS = {
    "rij": "a0000000-0000-4000-8000-000000000101",
    "vrijstaand": "a0000000-0000-4000-8000-000000000201",
    "schuur": "a0000000-0000-4000-8000-000000000202",
}
VBO_IDS = {"R1": "0363010000000011", "R2": "0363010000000012", "R3": "0363010000000013",
           "V": "0363010000000021"}  # fmt: skip
VHE_NUMMERS = {"R1": "100001", "R2": "100002", "R3": "100003", "V": "100004"}

# --- rasters (Task 9) ---------------------------------------------------------------
WMTS_OORSPRONG = (-285401.92, 903401.92)  # pixelraster luchtfoto's (matrixset EPSG:28992)
RASTER_RES = {"zomer_nu": 0.21, "zomer_vorig": 0.21, "cir_nu": 0.21, "cir_vorig": 0.21,
              "winter_nu": 0.105, "winter_vorig": 0.105}  # fmt: skip
AHN_RES = 0.5
AHN_NODATA = float(np.finfo(np.float32).max)  # 3.4028235e38
GROND_Z = -0.5  # maaiveld (DTM) overal, m NAP
GROENVLAK_R1 = (1, 20, 5, 30)  # achtertuin R1, begroeiing 1,0 m
GROENVLAK_R1_VORIG = (1, 20, 5, 25)  # vorige zomer: alleen de onderste helft groen
HAAG_R2 = (6.5, 1, 11.5, 3)  # voortuin R2 (y 2-3) + 1 m over het voetpad (y 1-2), 2,5 m
BLOK_P2 = (22, 22, 24, 24)  # achtertuin P2: 4 m², 2,0 m, niet groen; vorige zomer afwezig
HOOGTE = {"rij": 9.0, "vrijstaand": 7.0, "schuur": 2.5, "groen": 0.1, "groenvlak": 1.0,
          "haag": 2.5, "blok": 2.0}  # fmt: skip  # m boven maaiveld (DSM - DTM)
# kleuren per klasse: RGB (R, G, B) zomer, CIR (NIR, rood, groen), RGB winter (bladloos)
KLEUR_RGB = {"verharding": (128, 128, 128), "dak": (150, 70, 60), "gras": (90, 150, 60),
             "groen": (60, 140, 50), "blok": (90, 90, 90)}  # fmt: skip
KLEUR_CIR = {"verharding": (110, 120, 120), "dak": (90, 150, 70), "gras": (180, 80, 140),
             "groen": (200, 60, 130), "blok": (80, 90, 90)}  # fmt: skip
KLEUR_WINTER = {"verharding": (128, 128, 128), "dak": (150, 70, 60), "gras": (110, 120, 80),
                "groen": (120, 100, 80), "blok": (90, 90, 90)}  # fmt: skip
AHN_SLEUTELS = ("ahn_dtm", "ahn_dsm")

EXPORT_KOP = ["Eenheid", "BAG VBO", "Wijknaam", "Complexnr", "Soort woning", "Straat", "Huisnr",
              "Postcode", "Plaats"]  # fmt: skip
KOLOMMEN = {
    "vhe_nummer": "Eenheid",
    "bag_vbo_id": "BAG VBO",
    "wijk": "Wijknaam",
    "complex": "Complexnr",
    "woningtype": "Soort woning",
    "straat": "Straat",
    "huisnummer": "Huisnr",
    "postcode": "Postcode",
    "plaats": "Plaats",
}


@dataclass
class MiniWijk:
    cfg: Config
    mappen: Mappen
    config_pad: Path
    vhes: pd.DataFrame
    vbo: gpd.GeoDataFrame
    panden: gpd.GeoDataFrame
    percelen: gpd.GeoDataFrame
    wegdelen: gpd.GeoDataFrame
    begroeid: gpd.GeoDataFrame
    bbox: tuple[float, float, float, float]  # bbox in manifest (= gebied.bbox_rd + buffer)
    perceel_ids: dict[str, str] = field(default_factory=lambda: dict(PERCEEL_IDS))
    pand_ids: dict[str, str] = field(default_factory=lambda: dict(PAND_IDS))
    pand_feature_ids: dict[str, str] = field(default_factory=lambda: dict(PAND_FEATURE_IDS))
    vbo_ids: dict[str, str] = field(default_factory=lambda: dict(VBO_IDS))
    vhe_nummers: dict[str, str] = field(default_factory=lambda: dict(VHE_NUMMERS))
    rasters: dict[str, Path] = field(default_factory=dict)  # cache-sleutel -> GeoTIFF


def rd(x: float, y: float) -> tuple[float, float]:
    """Relatieve fixture-coördinaat naar RD."""
    return (OORSPRONG[0] + x, OORSPRONG[1] + y)


def rd_box(r: tuple[float, float, float, float]):
    (x0, y0), (x1, y1) = rd(r[0], r[1]), rd(r[2], r[3])
    return box(x0, y0, x1, y1)


def _vectoren() -> dict[str, gpd.GeoDataFrame]:
    def gdf(rijen, geoms):
        return gpd.GeoDataFrame(rijen, geometry=geoms, crs=28992)

    panden = gdf(
        [{"feature_id": PAND_FEATURE_IDS[p], "identificatie": PAND_IDS[p],
          "aantal_verblijfsobjecten": {"rij": 3, "vrijstaand": 1, "schuur": 0}[p],
          "gebruiksdoel": "overige gebruiksfunctie" if p == "schuur" else "woonfunctie"}
         for p in PANDEN],
        [rd_box(r) for r in PANDEN.values()],
    )  # fmt: skip
    vbo = gdf(
        [{"feature_id": f"b0000000-0000-4000-8000-{VBO_IDS[h][-12:]}",
          "identificatie": VBO_IDS[h], "gebruiksdoel": "woonfunctie",
          "pand.href": [PAND_ITEMS + PAND_FEATURE_IDS[pand]],
          "openbare_ruimte_naam": "Tuinstraat", "huisnummer": nr, "postcode": "1000AA",
          "woonplaats_naam": "Amsterdam"}
         for h, (_, pand, nr, _, _) in HUIZEN.items()],
        [Point(rd(*pt)) for pt, *_ in HUIZEN.values()],
    )  # fmt: skip
    percelen = gdf(
        [{"feature_id": f"c-{p}", "identificatie_lokaal_id": PERCEEL_IDS[p]} for p in PERCELEN],
        [MultiPolygon([rd_box(r)]) for r in PERCELEN.values()],
    )
    wegdelen = gdf(
        [{"feature_id": "w-1", "functie": "voetpad"},
         {"feature_id": "w-2", "functie": "rijbaan lokale weg"}],
        [rd_box(VOETPAD), rd_box(RIJBAAN)],
    )  # fmt: skip
    begroeid = gdf([{"feature_id": "g-1", "fysiek_voorkomen": "groenvoorziening"}],
                   [rd_box(GROEN)])  # fmt: skip
    return {"bag_pand": panden, "bag_vbo": vbo, "brk_perceel": percelen,
            "bgt_wegdeel": wegdelen, "bgt_begroeid": begroeid}  # fmt: skip


def _raster_vlakken(vorig: bool) -> list[tuple[str, tuple, str]]:
    """(klasse, relatieve rechthoek, hoogte-sleutel) in tekenvolgorde (later wint)."""
    vlakken = [("dak", PANDEN[p], p) for p in PANDEN]
    vlakken += [("gras", GROEN, "groen"),
                ("groen", GROENVLAK_R1_VORIG if vorig else GROENVLAK_R1, "groenvlak"),
                ("groen", HAAG_R2, "haag")]  # fmt: skip
    if not vorig:
        vlakken.append(("blok", BLOK_P2, "blok"))
    return vlakken


def _centra(x0: float, y1: float, res: float, b: int, h: int):
    """Pixelcentra in relatieve fixture-coördinaten (x 1-D, y 1-D)."""
    xs = x0 + (np.arange(b) + 0.5) * res - OORSPRONG[0]
    ys = y1 - (np.arange(h) + 0.5) * res - OORSPRONG[1]
    return xs, ys


def _in(xs, ys, r) -> np.ndarray:
    return ((ys >= r[1]) & (ys < r[3]))[:, None] & ((xs >= r[0]) & (xs < r[2]))[None, :]


def _schrijf_luchtfoto(pad: Path, sleutel: str, bbox) -> None:
    res = RASTER_RES[sleutel]
    ox, oy = WMTS_OORSPRONG
    c0, c1 = np.floor((bbox[0] - ox) / res), np.ceil((bbox[2] - ox) / res)
    r0, r1 = np.floor((oy - bbox[3]) / res), np.ceil((oy - bbox[1]) / res)
    x0, y1 = ox + c0 * res, oy - r0 * res
    b, h = int(c1 - c0), int(r1 - r0)
    xs, ys = _centra(x0, y1, res, b, h)
    kleur = (KLEUR_CIR if sleutel.startswith("cir_") else
             KLEUR_WINTER if sleutel.startswith("winter_") else KLEUR_RGB)  # fmt: skip
    data = np.empty((3, h, b), dtype=np.uint8)
    data[:] = np.array(kleur["verharding"], dtype=np.uint8)[:, None, None]
    for klasse, r, _ in _raster_vlakken(vorig=sleutel.endswith("_vorig")):
        data[:, _in(xs, ys, r)] = np.array(kleur[klasse], dtype=np.uint8)[:, None]
    pad.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        with rasterio.open(
            pad, "w", driver="GTiff", width=b, height=h, count=3, dtype="uint8",
            crs="EPSG:28992", transform=from_origin(x0, y1, res, res), nodata=0,
            tiled=True, blockxsize=256, blockysize=256, compress="deflate",
        ) as dst:  # fmt: skip
            dst.write(data)
            dst.write_mask(np.full((h, b), 255, dtype=np.uint8))


def _schrijf_ahn(pad: Path, sleutel: str, bbox) -> None:
    res = AHN_RES
    x0, x1 = np.floor(bbox[0] / res) * res, np.ceil(bbox[2] / res) * res
    y0, y1 = np.floor(bbox[1] / res) * res, np.ceil(bbox[3] / res) * res
    b, h = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
    xs, ys = _centra(x0, y1, res, b, h)
    data = np.full((h, b), GROND_Z, dtype=np.float32)
    for _, r, hoogte in _raster_vlakken(vorig=False):
        if sleutel == "ahn_dtm":
            if hoogte in PANDEN:
                data[_in(xs, ys, r)] = AHN_NODATA
        else:
            data[_in(xs, ys, r)] = GROND_Z + HOOGTE[hoogte]
    with rasterio.open(
        pad, "w", driver="GTiff", width=b, height=h, count=1, dtype="float32",
        crs="EPSG:28992", transform=from_origin(x0, y1, res, res), nodata=AHN_NODATA,
        tiled=True, blockxsize=256, blockysize=256, compress="deflate",
    ) as dst:  # fmt: skip
        dst.write(data[np.newaxis])


def _schrijf_rasters(cfg: Config, mappen: Mappen, manifest: Manifest, bbox) -> dict[str, Path]:
    """Synthetische GeoTIFFs in cache/raster + manifest, zoals de download-stap ze schrijft."""
    paden = {}
    for sleutel in LUCHTFOTO_SLEUTELS:
        pad = raster_pad(mappen.cache, sleutel)
        _schrijf_luchtfoto(pad, sleutel, bbox)
        url = cfg.luchtfoto.cir_wmts if sleutel.startswith("cir_") else cfg.luchtfoto.rgb_wmts
        spec = cfg.luchtfoto.lagen[sleutel]
        manifest.registreer(
            sleutel, url, bbox, {"laag": spec["laag"], "zoom": str(spec["zoom"]),
                                 "jaar": spec["jaar"], "tegels_totaal": 1, "tegels_mislukt": 0},
        )  # fmt: skip
        paden[sleutel] = pad
    for sleutel in AHN_SLEUTELS:
        pad = raster_pad(mappen.cache, sleutel)
        _schrijf_ahn(pad, sleutel, bbox)
        url = cfg.ahn.dtm_index if sleutel == "ahn_dtm" else cfg.ahn.dsm_index
        manifest.registreer(sleutel, url, bbox, {"kaartbladen": ["synthetisch"],
                                                 "versie": cfg.ahn.versie})  # fmt: skip
        paden[sleutel] = pad
    return paden


def _schrijf_export(invoer: Path, cfg: Config) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(EXPORT_KOP)
    for h, (_, _, nr, wtype, cplx) in HUIZEN.items():
        ws.append([int(VHE_NUMMERS[h]), VBO_IDS[h], "Testwijk", cplx, wtype, "Tuinstraat", nr,
                   "1000AA", "Amsterdam"])  # fmt: skip
    invoer.mkdir(parents=True, exist_ok=True)
    wb.save(invoer / cfg.tobias.bestand)
    (invoer / cfg.tobias.kolommen_bestand).write_text(
        yaml.safe_dump(KOLOMMEN, allow_unicode=True), encoding="utf-8"
    )


def maak_miniwijk(basis: Path) -> MiniWijk:
    """Schrijf config, export, kolommen.yaml, vector-GPKG's, raster-GeoTIFFs en manifest."""
    data = yaml.safe_load(VOORBEELD.read_text(encoding="utf-8"))
    data["gebied"] = {
        "naam": "miniwijk",
        "bbox_rd": [*rd(BBOX_REL[0], BBOX_REL[1]), *rd(BBOX_REL[2], BBOX_REL[3])],
        "buffer_m": BUFFER_M,
    }
    data["mappen"] = {k: str(basis / k) for k in ("invoer", "cache", "uitvoer")}
    basis.mkdir(parents=True, exist_ok=True)
    config_pad = basis / "tuinscan.yaml"
    config_pad.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    cfg = laad_config(config_pad)
    mappen = Mappen.uit_config(cfg, None)

    bbox = _grow(cfg)
    manifest = Manifest(manifest_pad(mappen.cache))
    gelezen = {}
    for sleutel, gdf in _vectoren().items():
        url_attr, coll = VECTOR_SLEUTELS[sleutel]
        pad = vector_pad(mappen.cache, sleutel)
        kolommen = schrijf_vector(gdf, pad)
        manifest.registreer(
            sleutel, getattr(cfg.pdok, url_attr), bbox,
            {"collectie": cfg.pdok.collecties[coll], "aantal": len(gdf),
             "json_kolommen": kolommen},
        )  # fmt: skip
        gelezen[sleutel] = lees_vector(pad)

    rasters = _schrijf_rasters(cfg, mappen, manifest, bbox)
    _schrijf_export(mappen.invoer, cfg)
    vhes = lees_vhes(cfg, mappen, Foutenlijst())
    return MiniWijk(
        cfg=cfg, mappen=mappen, config_pad=config_pad, vhes=vhes,
        vbo=gelezen["bag_vbo"], panden=gelezen["bag_pand"], percelen=gelezen["brk_perceel"],
        wegdelen=gelezen["bgt_wegdeel"], begroeid=gelezen["bgt_begroeid"], bbox=bbox,
        rasters=rasters,
    )  # fmt: skip
