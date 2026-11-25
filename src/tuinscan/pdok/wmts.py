"""WMTS-client: capabilities, tegelrekenen en mozaïek naar GeoTIFF (EPSG:28992)."""

from __future__ import annotations

import io
import math
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import requests
from PIL import Image
from rasterio.transform import from_origin

NS = {
    "w": "http://www.opengis.net/wmts/1.0",
    "ows": "http://www.opengis.net/ows/1.1",
}
PIXEL_M = 0.00028  # OGC-standaard pixelgrootte
TIMEOUT = 60


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
        lagen[laag.findtext("ows:Identifier", namespaces=NS)] = (
            laag.findtext("w:Format", namespaces=NS) or "image/jpeg"
        )
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


def _haal_tegel(sessie, url, laag, matrixset, m, col, row) -> np.ndarray | None:
    """(3,h,w) uint8, of None als de tegel mislukt, leeg-wit of leeg-zwart is."""
    try:
        resp = sessie.get(f"{url}/{laag}/{matrixset}/{m.id}/{col}/{row}.jpeg", timeout=TIMEOUT)
        if resp.status_code != 200:
            return None
        with Image.open(io.BytesIO(resp.content)) as im:
            arr = np.asarray(im.convert("RGB"))
    except (requests.RequestException, OSError):
        return None
    if (arr == 255).all() or (arr == 0).all():
        return None
    return arr.transpose(2, 0, 1)


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
    tegels = tegels_voor_bbox(matrix, bbox)
    if not tegels:
        raise ValueError("bbox valt buiten de tegelmatrix")
    c0, r0 = tegels[0][0], tegels[0][1]
    c1 = max(c for c, _ in tegels)
    r1 = max(r for _, r in tegels)
    tb, th = matrix.tegel_b, matrix.tegel_h
    data = np.zeros((3, (r1 - r0 + 1) * th, (c1 - c0 + 1) * tb), dtype=np.uint8)
    mask = np.zeros(data.shape[1:], dtype=np.uint8)

    def werk(cr: tuple[int, int]):
        return cr, _haal_tegel(sessie, url, laag, matrixset, matrix, *cr)

    mislukt = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for (c, r), arr in pool.map(werk, tegels):
            if arr is None or arr.shape != (3, th, tb):
                mislukt += 1
                continue
            y, x = (r - r0) * th, (c - c0) * tb
            data[:, y : y + th, x : x + tb] = arr
            mask[y : y + th, x : x + tb] = 255

    transform = from_origin(
        matrix.origin_x + c0 * tb * matrix.resolutie,
        matrix.origin_y - r0 * th * matrix.resolutie,
        matrix.resolutie,
        matrix.resolutie,
    )
    doel.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        with rasterio.open(
            doel, "w", driver="GTiff", width=data.shape[2], height=data.shape[1], count=3,
            dtype="uint8", crs="EPSG:28992", transform=transform, nodata=0,
            tiled=True, blockxsize=256, blockysize=256, compress="deflate",
        ) as dst:
            dst.write(data)
            dst.write_mask(mask)
    return MozaiekInfo(doel, len(tegels), mislukt)
