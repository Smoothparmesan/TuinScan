"""AHN: kaartbladindex en windowed COG-lezing naar een GeoTIFF."""

from __future__ import annotations

import math
import os
import urllib.request
from contextlib import ExitStack
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.windows import from_bounds
from shapely.geometry import box

from tuinscan.bestanden import schrijf_atomair


def kaartbladen_voor_bbox(index: gpd.GeoDataFrame, url_veld: str, bbox) -> list[str]:
    """URL's van kaartbladen die de bbox (RD) raken."""
    if index.crs is None:
        index = index.set_crs(28992)
    elif index.crs.to_epsg() != 28992:
        index = index.to_crs(28992)
    sel = index[index.geometry.intersects(box(*bbox))]
    return [str(u) for u in sel[url_veld]]


def gdal_proxy_opties(env=None, systeem=None) -> dict[str, str]:
    """GDAL-proxyopties (alleen indien niet al gezet).

    Proxy uit HTTPS_PROXY, anders uit de systeeminstellingen (`urllib.request.getproxies()`,
    op Windows het register), zoals `requests` dat ook doet; GDAL leest die zelf niet.
    """
    env = os.environ if env is None else env
    opt: dict[str, str] = {}
    proxy = env.get("HTTPS_PROXY") or env.get("https_proxy")
    if not proxy:
        systeem = urllib.request.getproxies() if systeem is None else systeem
        proxy = systeem.get("https")
    if proxy and not env.get("GDAL_HTTP_PROXY"):
        opt["GDAL_HTTP_PROXY"] = proxy
    ca = env.get("REQUESTS_CA_BUNDLE") or env.get("SSL_CERT_FILE")
    if ca:
        if not env.get("GDAL_CURL_CA_BUNDLE"):
            opt["GDAL_CURL_CA_BUNDLE"] = ca
        if not env.get("CURL_CA_BUNDLE"):
            opt["CURL_CA_BUNDLE"] = ca
    return opt


def _bron(b: str) -> str:
    return f"/vsicurl/{b}" if b.lower().startswith(("http://", "https://")) else b


def lees_ahn(bronnen: list[str], bbox, doel: Path) -> Path:
    """Lees het bbox-venster uit alle bronnen, voeg samen, schrijf float32 GeoTIFF."""
    if not bronnen:
        raise ValueError("geen AHN-kaartbladen voor deze bbox")
    doel = Path(doel)
    doel.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.Env(**gdal_proxy_opties()):
        with ExitStack() as stack:
            datasets = [stack.enter_context(rasterio.open(_bron(b))) for b in bronnen]
            nodata = datasets[0].nodata
            fill = np.float32(nodata if nodata is not None else np.finfo("float32").max)
            rx, ry = datasets[0].res
            t0 = datasets[0].transform
            bx0, by0, bx1, by1 = (float(v) for v in bbox)
            # snap naar het pixelraster van de eerste bron (naar buiten afgerond)
            minx = t0.c + math.floor((bx0 - t0.c) / rx + 1e-9) * rx
            maxx = t0.c + math.ceil((bx1 - t0.c) / rx - 1e-9) * rx
            maxy = t0.f - math.floor((t0.f - by1) / ry + 1e-9) * ry
            miny = t0.f - math.ceil((t0.f - by0) / ry - 1e-9) * ry
            b_ = max(1, round((maxx - minx) / rx))
            h_ = max(1, round((maxy - miny) / ry))
            transform = from_origin(minx, maxy, rx, ry)
            data = np.full((h_, b_), fill, dtype=np.float32)
            gevuld = np.zeros((h_, b_), dtype=bool)
            for d in datasets:
                venster = from_bounds(minx, miny, maxx, maxy, d.transform)
                deel = d.read(
                    1, window=venster, out_shape=(h_, b_), boundless=True,
                    masked=True, resampling=Resampling.nearest,
                )
                waarden = np.asarray(deel.data, dtype=np.float32)
                geldig = ~np.ma.getmaskarray(deel) & ~np.isnan(waarden)
                neem = ~gevuld & geldig
                if neem.any():
                    data[neem] = waarden[neem]
                    gevuld |= neem
        data = data[np.newaxis]
        nodata = float(fill)
        with schrijf_atomair(doel) as tmp:
            with rasterio.open(
                tmp, "w", driver="GTiff", width=data.shape[2], height=data.shape[1],
                count=1, dtype="float32", crs="EPSG:28992", transform=transform,
                nodata=nodata, tiled=True, blockxsize=256, blockysize=256, compress="deflate",
            ) as dst:
                dst.write(data)
    return doel
