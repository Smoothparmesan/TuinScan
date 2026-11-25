"""Rastervensters per tuin op een gemeenschappelijk grid (EPSG:28992, origin = veelvoud van res).

Alle vensters worden met `rasterio.warp.reproject` op hetzelfde grid gezet, ongeacht de
resolutie of uitlijning van de bron (WMTS-mozaïek 0,21 m, AHN 0,5 m). Resultaat is een
float32 `np.ma.MaskedArray` (banden, h, w); gemaskeerd = nodata, mislukte tegel (interne
GeoTIFF-mask) of buiten de bron.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import shapely
from affine import Affine
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.fill import fillnodata
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window, from_bounds
from shapely.geometry.base import BaseGeometry

from tuinscan.config import MAX_PANDMARGE_M
from tuinscan.download import LUCHTFOTO_SLEUTELS, manifest_pad, raster_pad
from tuinscan.mappen import Mappen
from tuinscan.pdok.cache import Manifest

Raster = np.ma.MaskedArray

CRS_RD = CRS.from_epsg(28992)
AHN_SLEUTELS = ("ahn_dtm", "ahn_dsm", "ahn_vorig_dtm", "ahn_vorig_dsm")
RASTER_SLEUTELS = (*LUCHTFOTO_SLEUTELS, *AHN_SLEUTELS)
VENSTER_MARGE_M = 1.0
DTM_ZOEKAFSTAND_PX = 40  # AHN-pixels; DTM heeft grote gaten onder gebouwen
_RAND_PX = 2  # extra bronpixels rond het venster voor bilineaire interpolatie
_EPS = 1e-6


def bronnen_uit_cache(mappen: Mappen) -> dict[str, Path | None]:
    """Cache-sleutel -> GeoTIFF; None als het bestand ontbreekt of het manifest 'leeg' zegt."""
    manifest = Manifest(manifest_pad(mappen.cache))._laad()
    bronnen: dict[str, Path | None] = {}
    for sleutel in RASTER_SLEUTELS:
        pad = raster_pad(mappen.cache, sleutel)
        leeg = manifest.get(sleutel, {}).get("status") == "leeg"
        bronnen[sleutel] = pad if pad.exists() and not leeg else None
    return bronnen


def grid(bounds, res: float) -> tuple[Affine, tuple[int, int]]:
    """Transform en (h, w) van het grid dat `bounds` omvat; origin = veelvoud van `res`."""
    minx, miny, maxx, maxy = (float(v) for v in bounds)
    x0 = math.floor(minx / res + _EPS) * res
    y0 = math.floor(miny / res + _EPS) * res
    x1 = math.ceil(maxx / res - _EPS) * res
    y1 = math.ceil(maxy / res - _EPS) * res
    b, h = max(1, round((x1 - x0) / res)), max(1, round((y1 - y0) / res))
    return from_origin(x0, y1, res, res), (h, b)


def _lees_bron(src, bounds, rand_px: int):
    """Bronvenster rond `bounds` (+ rand_px bronpixels) als float32 met NaN = ongeldig.

    Geeft (data, transform) of None als het venster buiten de bron valt. Ongeldig volgt
    `read_masks`: de interne mask (mislukte WMTS-tegels) of anders de nodata-waarde.
    """
    if src.crs is not None and CRS.from_user_input(src.crs) != CRS_RD:
        bounds = transform_bounds(CRS_RD, src.crs, *bounds)
    w = from_bounds(*bounds, transform=src.transform)
    c0 = max(0, math.floor(w.col_off) - rand_px)
    r0 = max(0, math.floor(w.row_off) - rand_px)
    c1 = min(src.width, math.ceil(w.col_off + w.width) + rand_px)
    r1 = min(src.height, math.ceil(w.row_off + w.height) + rand_px)
    if c1 <= c0 or r1 <= r0:
        return None
    venster = Window(c0, r0, c1 - c0, r1 - r0)
    data = src.read(window=venster).astype(np.float32)
    geldig = src.read_masks(window=venster) > 0
    data[~geldig] = np.nan
    return data, src.window_transform(venster)


def _naar_grid(data, src_transform, src_crs, transform, vorm, resampling) -> Raster:
    doel = np.full((data.shape[0], *vorm), np.nan, dtype=np.float32)
    reproject(
        source=data, destination=doel,
        src_transform=src_transform, src_crs=src_crs or CRS_RD, src_nodata=np.nan,
        dst_transform=transform, dst_crs=CRS_RD, dst_nodata=np.nan,
        resampling=resampling,
    )  # fmt: skip
    return np.ma.masked_invalid(doel, copy=False)


def _leeg(banden: int, vorm) -> Raster:
    return np.ma.masked_all((banden, *vorm), dtype=np.float32)


def _venster(pad: Path | None, transform, vorm, resampling, dtm_vullen: bool = False):
    if pad is None or not Path(pad).exists():
        return None
    h, b = vorm
    bounds = (transform.c, transform.f + transform.e * h, transform.c + transform.a * b,
              transform.f)  # fmt: skip
    with rasterio.open(pad) as src:
        rand = _RAND_PX + (DTM_ZOEKAFSTAND_PX if dtm_vullen else 0)
        gelezen = _lees_bron(src, bounds, rand)
        if gelezen is None:
            return _leeg(src.count, vorm)
        data, src_transform = gelezen
        if dtm_vullen:
            data = _vul_gaten(data)
        return _naar_grid(data, src_transform, src.crs, transform, vorm, resampling)


def _vul_gaten(data: np.ndarray) -> np.ndarray:
    """Vul NaN-gaten (DTM onder gebouwen); te verre pixels blijven NaN (= ongeldig)."""
    gevuld = data.copy()
    for i, band in enumerate(data):
        geldig = ~np.isnan(band)
        if geldig.any() and not geldig.all():
            gevuld[i] = fillnodata(
                band.copy(), mask=geldig.astype(np.uint8),
                max_search_distance=DTM_ZOEKAFSTAND_PX, smoothing_iterations=0,
            )  # fmt: skip
    return gevuld


def lees_venster(pad: Path | None, bounds, resolutie: float, resampling: Resampling):
    """Venster van `pad` op het grid rond `bounds`; None als `pad` None is of ontbreekt."""
    transform, vorm = grid(bounds, resolutie)
    return _venster(pad, transform, vorm, resampling)


@dataclass
class TuinRasters:
    transform: Affine
    vorm: tuple[int, int]
    tuin_masker: np.ndarray  # bool (h, w)
    zone_masker: np.ndarray  # bool (h, w), S3-overhangzone
    zomer_nu: Raster | None
    zomer_vorig: Raster | None
    cir_nu: Raster | None
    cir_vorig: Raster | None
    ndsm: Raster | None
    ndsm_vorig: Raster | None
    # m van het pixelcentrum tot het dichtstbijzijnde BAG-pand (0 = erin), alleen voor
    # tuinpixels; inf = buiten de tuin of geen pand binnen MAX_PANDMARGE_M; None = onbekend
    pand_afstand: np.ndarray | None = None


def _masker(geom: BaseGeometry | None, transform, vorm) -> np.ndarray:
    if geom is None or geom.is_empty:
        return np.zeros(vorm, dtype=bool)
    return rasterize([(geom, 1)], out_shape=vorm, transform=transform, fill=0,
                     dtype="uint8").astype(bool)  # fmt: skip


def _ndsm(dtm_pad, dsm_pad, transform, vorm) -> Raster | None:
    if dtm_pad is None or dsm_pad is None:
        return None
    dtm = _venster(dtm_pad, transform, vorm, Resampling.nearest, dtm_vullen=True)
    dsm = _venster(dsm_pad, transform, vorm, Resampling.nearest)
    if dtm is None or dsm is None:
        return None
    return dsm[:1] - dtm[:1]


def _lokaal(vlakken, gebied: BaseGeometry) -> list[BaseGeometry]:
    """Geometrieën uit `vlakken` die `gebied` raken; GeoDataFrame/GeoSeries via de sindex.

    Zo wordt per tuin alleen de directe omgeving bewerkt, nooit de hele wijk.
    """
    if vlakken is None:
        return []
    if isinstance(vlakken, BaseGeometry):
        kandidaten = [vlakken]
    elif hasattr(vlakken, "sindex"):
        geoms = getattr(vlakken, "geometry", vlakken)
        kandidaten = list(geoms.iloc[geoms.sindex.query(gebied, predicate="intersects")])
    else:
        kandidaten = [g for g in vlakken if g is not None and g.intersects(gebied)]
    return [g for g in kandidaten if g is not None and not g.is_empty]


def _pand_afstand(panden, tuin_masker: np.ndarray, transform) -> np.ndarray:
    """Afstand (m) van elk tuinpixelcentrum tot het dichtstbijzijnde pand; elders inf."""
    afstand = np.full(tuin_masker.shape, np.inf, dtype=np.float32)
    rijen, kolommen = np.nonzero(tuin_masker)
    if rijen.size == 0:
        return afstand
    xs, ys = transform @ (kolommen + 0.5, rijen + 0.5)
    zoek = shapely.box(xs.min(), ys.min(), xs.max(), ys.max()).buffer(MAX_PANDMARGE_M)
    dichtbij = _lokaal(panden, zoek)
    if dichtbij:
        d = shapely.distance(shapely.points(xs, ys), shapely.union_all(dichtbij))
        afstand[rijen, kolommen] = np.where(d <= MAX_PANDMARGE_M, d, np.inf)
    return afstand


def bouw_tuinrasters(
    tuin: BaseGeometry,
    zone: BaseGeometry | None,
    bronnen: dict[str, Path | None],
    res: float,
    panden=None,
) -> TuinRasters:
    """Alle rasters voor één tuin(deel) op het grid rond (tuin ∪ zone) + 1 m.

    Met `panden` (GeoDataFrame/GeoSeries met BAG-panden) ook de afstand tot het dichtstbij-
    zijnde pand per tuinpixel (scheve daken in de zomerfoto, hoogte-halo in AHN).
    """
    geheel = tuin if zone is None or zone.is_empty else shapely.union(tuin, zone)
    x0, y0, x1, y1 = geheel.bounds
    m = VENSTER_MARGE_M
    transform, vorm = grid((x0 - m, y0 - m, x1 + m, y1 + m), res)

    def foto(sleutel: str) -> Raster | None:
        return _venster(bronnen.get(sleutel), transform, vorm, Resampling.bilinear)

    tuin_masker = _masker(tuin, transform, vorm)
    return TuinRasters(
        transform=transform, vorm=vorm,
        tuin_masker=tuin_masker,
        zone_masker=_masker(zone, transform, vorm),
        zomer_nu=foto("zomer_nu"), zomer_vorig=foto("zomer_vorig"),
        cir_nu=foto("cir_nu"), cir_vorig=foto("cir_vorig"),
        ndsm=_ndsm(bronnen.get("ahn_dtm"), bronnen.get("ahn_dsm"), transform, vorm),
        ndsm_vorig=_ndsm(bronnen.get("ahn_vorig_dtm"), bronnen.get("ahn_vorig_dsm"),
                         transform, vorm),
        pand_afstand=None if panden is None else _pand_afstand(panden, tuin_masker, transform),
    )  # fmt: skip


def _unie(vlakken) -> BaseGeometry | None:
    if vlakken is None:
        return None
    if isinstance(vlakken, BaseGeometry):
        geom = vlakken
    else:  # GeoDataFrame / GeoSeries / lijst geometrieën
        geoms = getattr(vlakken, "geometry", vlakken)
        geoms = [g for g in geoms if g is not None and not g.is_empty]
        if not geoms:
            return None
        geom = shapely.union_all(geoms)
    return None if geom.is_empty else geom


def _alleen_vlakken(geom: BaseGeometry) -> BaseGeometry | None:
    delen = [g for g in shapely.get_parts(geom) if g.geom_type in ("Polygon", "MultiPolygon")]
    delen = [g for g in delen if g.area > 0]
    if not delen:
        return None
    return delen[0] if len(delen) == 1 else shapely.union_all(delen)


def overhang_zone(tuin, panden, bgt_voetpad, bgt_groen, buffer_m: float):
    """(tuin.buffer − tuin − panden) ∩ (voetpad ∪ openbaar groen); None als leeg.

    Alleen vlakken die de buffer raken tellen mee (sindex), zodat de kosten per tuin niet
    met de grootte van het werkgebied groeien.
    """
    zone = shapely.difference(tuin.buffer(buffer_m), tuin)
    publiek = _unie(_lokaal(bgt_voetpad, zone) + _lokaal(bgt_groen, zone))
    if publiek is None:
        return None
    gebouwd = _unie(_lokaal(panden, zone))
    if gebouwd is not None:
        zone = shapely.difference(zone, gebouwd)
    return _alleen_vlakken(shapely.intersection(zone, publiek))
