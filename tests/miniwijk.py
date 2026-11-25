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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import pandas as pd
import yaml
from openpyxl import Workbook
from shapely.geometry import MultiPolygon, Point, box

from tuinscan.config import Config, laad_config
from tuinscan.download import VECTOR_SLEUTELS, _grow, lees_vector, manifest_pad, schrijf_vector
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
    """Schrijf config, export, kolommen.yaml, vector-GPKG's en manifest onder `basis`."""
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

    _schrijf_export(mappen.invoer, cfg)
    vhes = lees_vhes(cfg, mappen, Foutenlijst())
    return MiniWijk(
        cfg=cfg, mappen=mappen, config_pad=config_pad, vhes=vhes,
        vbo=gelezen["bag_vbo"], panden=gelezen["bag_pand"], percelen=gelezen["brk_perceel"],
        wegdelen=gelezen["bgt_wegdeel"], begroeid=gelezen["bgt_begroeid"], bbox=bbox,
    )  # fmt: skip
