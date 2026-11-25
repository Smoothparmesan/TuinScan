"""OGC API Features-client voor PDOK (alleen bbox-verzoeken, nooit adressen)."""

from __future__ import annotations

import time

import geopandas as gpd
import requests
from shapely.geometry import shape

RD = "http://www.opengis.net/def/crs/EPSG/0/28992"
BACKOFF = (2, 4, 8)
TIMEOUT = 60


def _get(sessie: requests.Session, url: str, params: dict | None) -> dict:
    for poging in range(len(BACKOFF) + 1):
        laatste = poging == len(BACKOFF)
        try:
            resp = sessie.get(url, params=params, timeout=TIMEOUT)
        except (requests.ConnectionError, requests.Timeout):
            if laatste:
                raise
            time.sleep(BACKOFF[poging])
            continue
        if resp.status_code != 429 and resp.status_code < 500:
            resp.raise_for_status()
            return resp.json()
        if laatste:
            resp.raise_for_status()
        time.sleep(BACKOFF[poging])
    raise RuntimeError("onbereikbaar")  # pragma: no cover


def _getal(v: float) -> str:
    return f"{round(float(v), 3):.3f}".rstrip("0").rstrip(".")


def haal_features(
    basis_url: str,
    collectie: str,
    bbox: tuple[float, float, float, float],
    sessie: requests.Session,
    limit: int = 1000,
) -> gpd.GeoDataFrame:
    """Haal alle features binnen bbox (RD) op en volg `links[rel=next]`."""
    url: str | None = f"{basis_url}/collections/{collectie}/items"
    params: dict | None = {
        "bbox": ",".join(_getal(v) for v in bbox),
        "bbox-crs": RD,
        "crs": RD,
        "limit": limit,
        "f": "json",
    }
    rijen: list[dict] = []
    geoms = []
    while url:
        data = _get(sessie, url, params)
        for feat in data.get("features", []):
            rij = dict(feat.get("properties") or {})
            rij["feature_id"] = feat.get("id")
            rijen.append(rij)
            geoms.append(shape(feat["geometry"]) if feat.get("geometry") else None)
        url = next(
            (link["href"] for link in data.get("links", []) if link.get("rel") == "next"),
            None,
        )
        params = None  # next-link bevat al alle parameters
    if not rijen:
        return gpd.GeoDataFrame({"feature_id": []}, geometry=[], crs=28992)
    return gpd.GeoDataFrame(rijen, geometry=geoms, crs=28992)
