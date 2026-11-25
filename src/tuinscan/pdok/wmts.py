"""WMTS-client: capabilities, tegelrekenen en mozaïek naar GeoTIFF (EPSG:28992)."""

from __future__ import annotations

import io
import logging
import math
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import requests
from PIL import Image
from rasterio.transform import from_origin
from rasterio.windows import Window

from tuinscan.bestanden import schrijf_atomair

NS = {
    "w": "http://www.opengis.net/wmts/1.0",
    "ows": "http://www.opengis.net/ows/1.1",
}
PIXEL_M = 0.00028  # OGC-standaard pixelgrootte
TIMEOUT = 60
RETRIES = 2
BACKOFF = (1, 3)
LEEG_DREMPEL = 0.995  # aandeel pixels dat 'leeg' moet zijn (JPEG-ruis)
EXTENSIES = {"image/jpeg": "jpeg", "image/png": "png"}

log = logging.getLogger(__name__)


def _slaap(seconden: float) -> None:
    time.sleep(seconden)


@dataclass(frozen=True)
class TileMatrix:
    id: str
    resolutie: float
    origin_x: float
    origin_y: float
    tegel_b: int
    tegel_h: int
    matrix_b: int
    matrix_h: int


@dataclass(frozen=True)
class Capabilities:
    lagen: dict[str, str]  # laag-id -> tegelformaat (bv. image/jpeg)
    matrices: dict[str, TileMatrix]


@dataclass(frozen=True)
class MozaiekInfo:
    pad: Path
    tegels_totaal: int
    tegels_mislukt: int


def lees_capabilities(xml: bytes, matrixset: str) -> Capabilities:
    root = ET.fromstring(xml)
    lagen: dict[str, str] = {}
    for laag in root.iterfind("w:Contents/w:Layer", NS):
        sets = {e.text for e in laag.iterfind("w:TileMatrixSetLink/w:TileMatrixSet", NS)}
        if matrixset not in sets:
            continue
        laag_id = laag.findtext("ows:Identifier", namespaces=NS)
        if not laag_id:
            continue
        lagen[laag_id] = laag.findtext("w:Format", namespaces=NS) or "image/jpeg"
    matrices: dict[str, TileMatrix] = {}
    for tms in root.iterfind("w:Contents/w:TileMatrixSet", NS):
        if tms.findtext("ows:Identifier", namespaces=NS) != matrixset:
            continue
        for tm in tms.iterfind("w:TileMatrix", NS):
            tid = tm.findtext("ows:Identifier", namespaces=NS)
            x, y = (float(v) for v in tm.findtext("w:TopLeftCorner", namespaces=NS).split())
            matrices[tid] = TileMatrix(
                id=tid,
                resolutie=float(tm.findtext("w:ScaleDenominator", namespaces=NS)) * PIXEL_M,
                origin_x=x,
                origin_y=y,
                tegel_b=int(tm.findtext("w:TileWidth", namespaces=NS)),
                tegel_h=int(tm.findtext("w:TileHeight", namespaces=NS)),
                matrix_b=int(tm.findtext("w:MatrixWidth", namespaces=NS)),
                matrix_h=int(tm.findtext("w:MatrixHeight", namespaces=NS)),
            )
    return Capabilities(lagen, matrices)


def haal_capabilities(
    url: str, matrixset: str = "EPSG:28992", sessie: requests.Session | None = None
) -> Capabilities:
    resp = (sessie or requests).get(
        url, params={"request": "GetCapabilities", "service": "WMTS"}, timeout=TIMEOUT
    )
    resp.raise_for_status()
    return lees_capabilities(resp.content, matrixset)


def lijst_lagen(url: str) -> list[str]:
    return list(haal_capabilities(url).lagen)


def tegels_voor_bbox(
    m: TileMatrix, bbox: tuple[float, float, float, float]
) -> list[tuple[int, int]]:
    """(col, row)-paren die de bbox (RD) volledig dekken, rij voor rij."""
    minx, miny, maxx, maxy = bbox
    tw = m.resolutie * m.tegel_b
    th = m.resolutie * m.tegel_h
    c0 = max(0, math.floor((minx - m.origin_x) / tw))
    c1 = min(m.matrix_b - 1, math.floor((maxx - m.origin_x) / tw))
    r0 = max(0, math.floor((m.origin_y - maxy) / th))
    r1 = min(m.matrix_h - 1, math.floor((m.origin_y - miny) / th))
    return [(c, r) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]


def _is_leeg(arr: np.ndarray) -> bool:
    """Wit (alle banden >= 250) of zwart (alle banden <= 5) voor >= 99,5 % van de pixels."""
    wit = (arr >= 250).all(axis=2).mean()
    zwart = (arr <= 5).all(axis=2).mean()
    return wit >= LEEG_DREMPEL or zwart >= LEEG_DREMPEL


def _haal_tegel(sessie, url, laag, matrixset, m, col, row, ext="jpeg") -> np.ndarray | None:
    """(3,h,w) uint8, of None als de tegel mislukt of leeg (wit/zwart) is."""
    tegel_url = f"{url.rstrip('/')}/{laag}/{matrixset}/{m.id}/{col}/{row}.{ext}"
    reden = ""
    for poging in range(RETRIES + 1):
        try:
            resp = sessie.get(tegel_url, timeout=TIMEOUT)
        except (requests.Timeout, requests.ConnectionError) as exc:
            reden = type(exc).__name__
        except requests.RequestException as exc:
            reden = type(exc).__name__
            break
        else:
            if resp.status_code == 200:
                try:
                    with Image.open(io.BytesIO(resp.content)) as im:
                        arr = np.asarray(im.convert("RGB"))
                except OSError:
                    reden = "geen geldige afbeelding"
                    break
                if _is_leeg(arr):
                    reden = "lege (witte/zwarte) tegel"
                    break
                return arr.transpose(2, 0, 1)
            reden = f"HTTP {resp.status_code}"
            if resp.status_code != 429 and resp.status_code < 500:
                break
        if poging < RETRIES:
            _slaap(BACKOFF[poging])
    log.warning("tegel mislukt: laag=%s col=%s row=%s reden=%s", laag, col, row, reden)
    return None


def download_mozaiek(
    url: str,
    laag: str,
    matrixset: str,
    matrix: TileMatrix,
    formaat: str,
    bbox: tuple[float, float, float, float],
    doel: Path,
    sessie: requests.Session,
    workers: int,
) -> MozaiekInfo:
    ext = EXTENSIES.get(formaat, "jpeg")
    tegels = tegels_voor_bbox(matrix, bbox)
    if not tegels:
        raise ValueError("bbox valt buiten de tegelmatrix")
    c0, r0 = tegels[0][0], tegels[0][1]
    c1 = max(c for c, _ in tegels)
    r1 = max(r for _, r in tegels)
    tb, th = matrix.tegel_b, matrix.tegel_h
    breedte, hoogte = (c1 - c0 + 1) * tb, (r1 - r0 + 1) * th
    transform = from_origin(
        matrix.origin_x + c0 * tb * matrix.resolutie,
        matrix.origin_y - r0 * th * matrix.resolutie,
        matrix.resolutie,
        matrix.resolutie,
    )

    # requests.Session is niet thread-safe: geef elke worker een eigen gekloonde sessie
    lokaal = threading.local()
    gemaakte_sessies: list[requests.Session] = []
    slot = threading.Lock()
    echte_sessie = type(sessie) is requests.Session

    def _kloon_sessie() -> requests.Session:
        s = requests.Session()
        s.headers.update(sessie.headers)
        s.proxies.update(sessie.proxies)
        s.verify = sessie.verify
        s.cert = sessie.cert
        s.auth = sessie.auth
        s.trust_env = sessie.trust_env
        return s

    def worker_sessie():
        if not echte_sessie:
            return sessie
        s = getattr(lokaal, "sessie", None)
        if s is None:
            s = _kloon_sessie()
            lokaal.sessie = s
            with slot:
                gemaakte_sessies.append(s)
        return s

    def werk(cr: tuple[int, int]):
        return cr, _haal_tegel(worker_sessie(), url, laag, matrixset, matrix, *cr, ext)

    leeg_data = np.zeros((3, th, tb), dtype=np.uint8)
    leeg_mask = np.zeros((th, tb), dtype=np.uint8)
    vol_mask = np.full((th, tb), 255, dtype=np.uint8)
    mislukt = 0
    try:
        with schrijf_atomair(doel) as tmp:
            with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
                with rasterio.open(
                    tmp, "w", driver="GTiff", width=breedte, height=hoogte, count=3,
                    dtype="uint8", crs="EPSG:28992", transform=transform, nodata=0,
                    tiled=True, blockxsize=256, blockysize=256, compress="deflate",
                ) as dst:
                    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                        for (c, r), arr in pool.map(werk, tegels):
                            venster = Window((c - c0) * tb, (r - r0) * th, tb, th)
                            if arr is None or arr.shape != (3, th, tb):
                                mislukt += 1
                                dst.write(leeg_data, window=venster)
                                dst.write_mask(leeg_mask, window=venster)
                            else:
                                dst.write(arr, window=venster)
                                dst.write_mask(vol_mask, window=venster)
    finally:
        for s in gemaakte_sessies:
            s.close()
    return MozaiekInfo(doel, len(tegels), mislukt)
