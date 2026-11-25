"""Stap `download`: PDOK-data voor het werkgebied (alleen bbox-verzoeken)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import requests
from rasterio.errors import RasterioIOError

from tuinscan.bestanden import schrijf_atomair
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
    mislukt: dict[str, str] = field(default_factory=dict)


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
    with schrijf_atomair(pad) as tmp:
        gdf.to_file(tmp, driver="GPKG")
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
        with schrijf_atomair(doel) as tmp:
            tmp.write_bytes(resp.content)
    return gpd.read_file(doel)


def _ahn_bronnen(cfg: Config) -> list[tuple[str, str, str]]:
    """(sleutel, index-url, versie) voor AHN-rasters."""
    a = cfg.ahn
    bronnen = [("ahn_dtm", a.dtm_index, a.versie), ("ahn_dsm", a.dsm_index, a.versie)]
    if a.vorige:
        bronnen += [
            ("ahn_vorig_dtm", a.vorige["dtm_index"], a.vorige["versie"]),
            ("ahn_vorig_dsm", a.vorige["dsm_index"], a.vorige["versie"]),
        ]
    return bronnen


def voer_download_uit(cfg: Config, mappen: Mappen, vernieuw: bool) -> DownloadRapport:
    with requests.Session() as sessie:
        return _download_met_sessie(cfg, mappen, vernieuw, sessie)


def _download_met_sessie(
    cfg: Config, mappen: Mappen, vernieuw: bool, sessie: requests.Session
) -> DownloadRapport:
    bbox = _grow(cfg)
    rapport = DownloadRapport()
    manifest = Manifest(manifest_pad(mappen.cache))

    def klaar(sleutel: str, pad: Path, verwacht: dict) -> bool:
        """Cache bruikbaar: zelfde bbox, niet leeg en dezelfde bron (url, laag, zoom, ...)."""
        if vernieuw or not pad.exists() or not manifest.heeft(sleutel, bbox):
            return False
        rec = manifest.lees(sleutel)
        if rec.get("status") == "leeg":
            return False
        anders = {k: v for k, v in verwacht.items() if rec.get(k) != v}
        if anders:
            verschil = ", ".join(f"{k} {rec.get(k)!r} -> {v!r}" for k, v in anders.items())
            log.info("%s: cache past niet bij de config (%s); opnieuw ophalen", sleutel, verschil)
            return False
        rapport.overgeslagen.append(sleutel)
        return True

    for sleutel, (url_attr, coll) in VECTOR_SLEUTELS.items():
        pad = vector_pad(mappen.cache, sleutel)
        url = getattr(cfg.pdok, url_attr)
        if klaar(sleutel, pad, {"url": url, "collectie": cfg.pdok.collecties[coll]}):
            continue
        try:
            gdf = haal_features(url, cfg.pdok.collecties[coll], bbox, sessie)
            kolommen = schrijf_vector(gdf, pad)
        except (ValueError, OSError, requests.RequestException) as e:
            rapport.mislukt[sleutel] = str(e)
            log.warning("%s: mislukt (%s)", sleutel, e)
            continue
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
        url = cfg.luchtfoto.cir_wmts if sleutel.startswith("cir_") else cfg.luchtfoto.rgb_wmts
        bron = {"url": url, "laag": spec["laag"], "zoom": str(spec.get("zoom")),
                "jaar": spec.get("jaar")}  # fmt: skip
        if klaar(sleutel, pad, bron):
            continue
        try:
            if url not in caps:
                caps[url] = haal_capabilities(url, cfg.luchtfoto.matrixset, sessie)
            c = caps[url]
            if spec["laag"] not in c.lagen:
                raise KeyError(f"laag {spec['laag']} niet in capabilities")
            if str(spec["zoom"]) not in c.matrices:
                raise KeyError(f"zoom {spec['zoom']} niet in matrixset")
            info = download_mozaiek(
                url, spec["laag"], cfg.luchtfoto.matrixset, c.matrices[str(spec["zoom"])],
                c.lagen[spec["laag"]], bbox, pad, sessie, cfg.pdok.workers,
            )
        except (KeyError, ValueError, OSError, requests.RequestException) as e:
            rapport.mislukt[sleutel] = str(e)
            log.warning("%s: mislukt (%s)", sleutel, e)
            continue
        extra = {**bron, "tegels_totaal": info.tegels_totaal,
                 "tegels_mislukt": info.tegels_mislukt}  # fmt: skip
        if info.tegels_totaal and info.tegels_mislukt == info.tegels_totaal:
            extra["status"] = "leeg"
            rapport.leeg.append(sleutel)
            log.warning("%s: alle %d tegels ontbreken (laag %s)",
                        sleutel, info.tegels_totaal, spec["laag"])
        manifest.registreer(sleutel, url, bbox, extra)
        rapport.opgehaald.append(sleutel)

    for sleutel, index_url, versie in _ahn_bronnen(cfg):
        pad = raster_pad(mappen.cache, sleutel)
        if klaar(sleutel, pad, {"url": index_url, "versie": versie}):
            continue
        try:
            # ook de index opnieuw: een gecachte index hoort mogelijk bij een andere URL
            index = _laad_index(
                index_url, mappen.cache / "raster" / f"{sleutel}_index.json", sessie, True
            )
            bladen = kaartbladen_voor_bbox(index, cfg.ahn.url_veld, bbox)
            if not bladen:
                raise ValueError("geen AHN-kaartbladen voor deze bbox")
            lees_ahn(bladen, bbox, pad)
        except (ValueError, KeyError, RasterioIOError, requests.RequestException) as e:
            rapport.mislukt[sleutel] = str(e)
            log.warning("%s: mislukt (%s)", sleutel, e)
            continue
        manifest.registreer(sleutel, index_url, bbox, {"kaartbladen": bladen, "versie": versie})
        rapport.opgehaald.append(sleutel)
    return rapport


def _bruikbaar(mappen: Mappen, sleutel: str, rec: dict | None) -> bool:
    return (rec is not None and rec.get("status") != "leeg"
            and raster_pad(mappen.cache, sleutel).exists())  # fmt: skip


def herkomst_uit_cache(cfg: Config, mappen: Mappen) -> dict:
    """Herkomst van de rasters die echt in de cache liggen (manifest), niet uit de config.

    Ontbreekt een veld in een oud manifest, dan geldt de config-waarde alleen als de laag
    (of AHN-index-URL) gelijk is. `afwijkingen` noemt lagen waar cache en config verschillen.
    """
    data = Manifest(manifest_pad(mappen.cache))._laad()
    lagen, afwijkingen = {}, []
    for sleutel in LUCHTFOTO_SLEUTELS:
        rec = data.get(sleutel)
        if not _bruikbaar(mappen, sleutel, rec):
            continue
        spec = cfg.luchtfoto.lagen.get(sleutel) or {}
        gelijk = rec.get("laag") == spec.get("laag")
        jaar = rec.get("jaar", spec.get("jaar") if gelijk else None)
        zoom = rec.get("zoom", str(spec.get("zoom")) if gelijk else None)
        lagen[sleutel] = {"laag": rec.get("laag"), "jaar": jaar, "zoom": zoom}
        if not gelijk or (spec.get("jaar") is not None and jaar != spec.get("jaar")):
            afwijkingen.append(
                f"{sleutel}: cache bevat {rec.get('laag')} ({jaar}), config zegt "
                f"{spec.get('laag')} ({spec.get('jaar')}); draai 'tuinscan download'"
            )
    versies, bladen = [], []
    for sleutel, index_url, versie in _ahn_bronnen(cfg)[:2]:
        rec = data.get(sleutel)
        if not _bruikbaar(mappen, sleutel, rec):
            continue
        versies.append(rec.get("versie", versie if rec.get("url") == index_url else None))
        bladen += [Path(str(b)).stem for b in rec.get("kaartbladen", [])]
    return {
        "zomer_jaar": lagen.get("zomer_nu", {}).get("jaar"),
        "zomer_vorig_jaar": lagen.get("zomer_vorig", {}).get("jaar"),
        "winter_jaar": lagen.get("winter_nu", {}).get("jaar"),
        "ahn_versie": next((v for v in versies if v), None) if len(versies) == 2 else None,
        "lagen": lagen,
        "ahn_kaartbladen": sorted(set(bladen)),
        "afwijkingen": afwijkingen,
    }


def druk_lagen_af(cfg: Config) -> None:
    for naam, url in (("RGB", cfg.luchtfoto.rgb_wmts), ("CIR", cfg.luchtfoto.cir_wmts)):
        print(f"{naam} ({url}):")
        for laag in lijst_lagen(url):
            print(f"  {laag}")
