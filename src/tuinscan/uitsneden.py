"""Uitsneden van luchtfoto's rond een tuin: thumbnails en detailbeelden met tuincontour.

Geen adrestekst op de beelden; bestandsnamen bevatten alleen het VHE-nummer.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.errors import RasterioIOError
from rasterio.windows import from_bounds
from shapely.geometry.base import BaseGeometry

log = logging.getLogger(__name__)

BUFFER_M = 5.0
THUMB_PX = 200
DETAIL_PX = 800
CONTOUR_KLEUR = (255, 212, 0)  # #FFD400
CONTOUR_BREEDTE = 2
GEMASKEERD_KLEUR = (221, 221, 221)  # #DDDDDD
JPEG_KWALITEIT = 85

UITSNEDEN = {
    "thumb_zomer_nu": ("zomer_nu", THUMB_PX),
    "thumb_zomer_vorig": ("zomer_vorig", THUMB_PX),
    "detail_winter_nu": ("winter_nu", DETAIL_PX),
    "detail_winter_vorig": ("winter_vorig", DETAIL_PX),
    "detail_zomer_nu": ("zomer_nu", DETAIL_PX),
    "detail_zomer_vorig": ("zomer_vorig", DETAIL_PX),
}


def _ringen(geom: BaseGeometry):
    for deel in getattr(geom, "geoms", [geom]):
        if deel.geom_type != "Polygon" or deel.is_empty:
            continue
        yield deel.exterior.coords
        for binnen in deel.interiors:
            yield binnen.coords


def maak_uitsnede(
    bron: Path | None, tuin_geom: BaseGeometry, buffer_m: float, max_px: int, doel: Path
) -> Path | None:
    """RGB-uitsnede van bounds + buffer, langste zijde `max_px`, met gele tuincontour."""
    if bron is None or not Path(bron).exists() or tuin_geom is None or tuin_geom.is_empty:
        return None
    minx, miny, maxx, maxy = tuin_geom.bounds
    bounds = (minx - buffer_m, miny - buffer_m, maxx + buffer_m, maxy + buffer_m)
    try:
        with rasterio.open(bron) as src:
            venster = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
            if venster.width < 1 or venster.height < 1:
                return None
            data = src.read(window=venster, boundless=True, masked=True)
            transform = src.window_transform(venster)
    except RasterioIOError as fout:
        log.warning("uitsnede overgeslagen, bron onleesbaar (%s): %s", Path(bron).name, fout)
        return None
    if data.shape[0] < 3 or np.ma.getmaskarray(data[:3]).all():
        return None
    rgb = np.ma.filled(data[:3].astype(np.float32), 0).clip(0, 255).astype(np.uint8)
    ongeldig = np.ma.getmaskarray(data[:3]).any(axis=0)
    rgb = np.moveaxis(rgb, 0, -1).copy()
    rgb[ongeldig] = GEMASKEERD_KLEUR

    h, b = rgb.shape[:2]
    schaal = max_px / max(h, b)
    nieuw = (max(1, round(b * schaal)), max(1, round(h * schaal)))
    beeld = Image.fromarray(rgb).resize(nieuw, Image.LANCZOS)

    teken = ImageDraw.Draw(beeld)
    inv = ~transform
    for coords in _ringen(tuin_geom):
        punten = []
        for x, y, *_ in coords:
            kol, rij = inv @ (x, y)
            punten.append((kol * nieuw[0] / b, rij * nieuw[1] / h))
        teken.line(punten, fill=CONTOUR_KLEUR, width=CONTOUR_BREEDTE, joint="curve")

    doel = Path(doel)
    doel.parent.mkdir(parents=True, exist_ok=True)
    beeld.save(doel, "JPEG", quality=JPEG_KWALITEIT)
    return doel


def maak_uitsneden(
    vhe_nummer: str, tuin_geom: BaseGeometry, bronnen: dict[str, Path | None], map: Path
) -> dict[str, Path | None]:
    """Thumbnails en detailbeelden voor één tuin in `map`/uitsneden/{vhe}_{sleutel}.jpg."""
    uit: dict[str, Path | None] = {}
    for sleutel, (bronsleutel, max_px) in UITSNEDEN.items():
        doel = Path(map) / "uitsneden" / f"{vhe_nummer}_{sleutel}.jpg"
        uit[sleutel] = maak_uitsnede(bronnen.get(bronsleutel), tuin_geom, BUFFER_M, max_px, doel)
    return uit
