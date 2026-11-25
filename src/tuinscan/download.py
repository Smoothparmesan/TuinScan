"""Stap `download`: PDOK-data voor het werkgebied (alleen bbox-verzoeken)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import requests

from tuinscan.config import Config
from tuinscan.mappen import Mappen
from tuinscan.pdok.ahn import kaartbladen_voor_bbox, lees_ahn
from tuinscan.pdok.cache import Manifest, vector_pad
from tuinscan.pdok.ogc import haal_features
from tuinscan.pdok.wmts import download_mozaiek, haal_capabilities, lijst_lagen

log = logging.getLogger("tuinscan")

VECTOR_SLEUTELS = {  # sleutel -> (basis-url attribuut, collectie-sleutel)
    "bag_pand": ("bag_url", "pand"),
    "bag_vbo": ("bag_url", "verblijfsobject"),
    "brk_perceel": ("brk_url", "perceel"),
    "bgt_wegdeel": ("bgt_url", "wegdeel"),
    "bgt_begroeid": ("bgt_url", "begroeid"),
}
LUCHTFOTO_SLEUTELS = ("zomer_nu", "zomer_vorig", "cir_nu", "cir_vorig", "winter_nu", "winter_vorig")


@dataclass
class DownloadRapport:
    opgehaald: list[str] = field(default_factory=list)
    overgeslagen: list[str] = field(default_factory=list)
    niet_geconfigureerd: list[str] = field(default_factory=list)
    leeg: list[str] = field(default_factory=list)


def manifest_pad(cache: Path) -> Path:
    return cache / "manifest.json"


def raster_pad(cache: Path, sleutel: str) -> Path:
    return cache / "raster" / f"{sleutel}.tif"


def schrijf_vector(gdf: gpd.GeoDataFrame, pad: Path) -> list[str]:
    """Schrijf GPKG; lijstkolommen worden JSON-tekst. Geeft de gecodeerde kolomnamen."""
    gdf = gdf.copy()
    gecodeerd = []
    for kol in gdf.columns:
        if kol == gdf.geometry.name:
            continue
        if gdf[kol].map(lambda v: isinstance(v, (list, tuple))).any():
            gdf[kol] = gdf[kol].map(
                lambda v: json.dumps(list(v)) if isinstance(v, (list, tuple)) else v
            )
            gecodeerd.append(kol)
    pad.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(pad, driver="GPKG")
    return gecodeerd


def lees_vector(pad: Path) -> gpd.GeoDataFrame:
    """Lees een gecachte vectorlaag en zet JSON-kolommen terug naar lijsten."""
    pad = Path(pad)
    gdf = gpd.read_file(pad)
    rec = Manifest(manifest_pad(pad.parent.parent))._laad().get(pad.stem, {})
    for kol in rec.get("json_kolommen", []):
        if kol in gdf.columns:
            gdf[kol] = gdf[kol].map(lambda v: json.loads(v) if isinstance(v, str) else v)
    return gdf


def _grow(cfg: Config) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = cfg.gebied.bbox_rd
    b = cfg.gebied.buffer_m
    return (minx - b, miny - b, maxx + b, maxy + b)


def _laad_index(url: str, doel: Path, sessie: requests.Session, vernieuw: bool) -> gpd.GeoDataFrame:
    if vernieuw or not doel.exists():
        resp = sessie.get(url, timeout=60)
        resp.raise_for_status()
        doel.parent.mkdir(parents=True, exist_ok=True)
        doel.write_bytes(resp.content)
    return gpd.read_file(doel)


def _ahn_bronnen(cfg: Config) -> list[tuple[str, str]]:
    """(sleutel, index-url) voor AHN-rasters."""
    bronnen = [("ahn_dtm", cfg.ahn.dtm_index), ("ahn_dsm", cfg.ahn.dsm_index)]
    if cfg.ahn.vorige:
        bronnen += [
            ("ahn_vorig_dtm", cfg.ahn.vorige["dtm_index"]),
            ("ahn_vorig_dsm", cfg.ahn.vorige["dsm_index"]),
        ]
    return bronnen


def voer_download_uit(cfg: Config, mappen: Mappen, vernieuw: bool) -> DownloadRapport:
    bbox = _grow(cfg)
    rapport = DownloadRapport()
    manifest = Manifest(manifest_pad(mappen.cache))
    sessie = requests.Session()

    def klaar(sleutel: str, pad: Path) -> bool:
        if not vernieuw and pad.exists() and manifest.heeft(sleutel, bbox):
            rapport.overgeslagen.append(sleutel)
            return True
        return False

    for sleutel, (url_attr, coll) in VECTOR_SLEUTELS.items():
        pad = vector_pad(mappen.cache, sleutel)
        if klaar(sleutel, pad):
            continue
        url = getattr(cfg.pdok, url_attr)
        gdf = haal_features(url, cfg.pdok.collecties[coll], bbox, sessie)
        kolommen = schrijf_vector(gdf, pad)
        manifest.registreer(
            sleutel, url, bbox, {"collectie": cfg.pdok.collecties[coll],
                                 "aantal": len(gdf), "json_kolommen": kolommen}
        )
        rapport.opgehaald.append(sleutel)

    caps: dict[str, object] = {}
    for sleutel in LUCHTFOTO_SLEUTELS:
        spec = cfg.luchtfoto.lagen.get(sleutel) or {}
        if not spec.get("laag"):
            rapport.niet_geconfigureerd.append(sleutel)
            continue
        pad = raster_pad(mappen.cache, sleutel)
        if klaar(sleutel, pad):
            continue
        url = cfg.luchtfoto.cir_wmts if sleutel.startswith("cir_") else cfg.luchtfoto.rgb_wmts
        if url not in caps:
            caps[url] = haal_capabilities(url, cfg.luchtfoto.matrixset, sessie)
        c = caps[url]
        matrix = c.matrices[str(spec["zoom"])]
        info = download_mozaiek(
            url, spec["laag"], cfg.luchtfoto.matrixset, matrix,
            c.lagen[spec["laag"]], bbox, pad, sessie, cfg.pdok.workers,
        )
        extra = {"laag": spec["laag"], "tegels_totaal": info.tegels_totaal,
                 "tegels_mislukt": info.tegels_mislukt}
        if info.tegels_totaal and info.tegels_mislukt == info.tegels_totaal:
            extra["status"] = "leeg"
            rapport.leeg.append(sleutel)
            log.warning("%s: alle %d tegels ontbreken (laag %s)",
                        sleutel, info.tegels_totaal, spec["laag"])
        manifest.registreer(sleutel, url, bbox, extra)
        rapport.opgehaald.append(sleutel)

    for sleutel, index_url in _ahn_bronnen(cfg):
        pad = raster_pad(mappen.cache, sleutel)
        if klaar(sleutel, pad):
            continue
        index = _laad_index(
            index_url, mappen.cache / "raster" / f"{sleutel}_index.json", sessie, vernieuw
        )
        bladen = kaartbladen_voor_bbox(index, cfg.ahn.url_veld, bbox)
        lees_ahn(bladen, bbox, pad)
        manifest.registreer(sleutel, index_url, bbox, {"kaartbladen": bladen})
        rapport.opgehaald.append(sleutel)
    return rapport


def druk_lagen_af(cfg: Config) -> None:
    for naam, url in (("RGB", cfg.luchtfoto.rgb_wmts), ("CIR", cfg.luchtfoto.cir_wmts)):
        print(f"{naam} ({url}):")
        for laag in lijst_lagen(url):
            print(f"  {laag}")
