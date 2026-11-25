import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from tuinscan.pdok.ahn import gdal_proxy_opties, kaartbladen_voor_bbox, lees_ahn

NODATA = 3.4028234663852886e38


def test_kaartbladen_selected_by_intersection():
    idx = gpd.GeoDataFrame(
        {"url": ["a.tif", "b.tif", "c.tif"]},
        geometry=[box(0, 0, 100, 100), box(100, 0, 200, 100), box(500, 0, 600, 100)],
        crs=28992,
    )
    assert kaartbladen_voor_bbox(idx, "url", (90, 10, 110, 20)) == ["a.tif", "b.tif"]


def _tif(pad, x0, waarde):
    data = np.full((1, 40, 40), waarde, dtype="float32")
    with rasterio.open(
        pad, "w", driver="GTiff", width=40, height=40, count=1, dtype="float32",
        crs="EPSG:28992", transform=from_origin(x0, 20, 0.5, 0.5), nodata=NODATA,
    ) as d:
        d.write(data)


def test_ahn_merge_window(tmp_path):
    _tif(tmp_path / "a.tif", 0, 1.0)
    _tif(tmp_path / "b.tif", 20, 2.0)
    bbox = (15.0, 5.0, 25.0, 15.0)
    bronnen = [str(tmp_path / "a.tif"), str(tmp_path / "b.tif")]
    uit = lees_ahn(bronnen, bbox, tmp_path / "o" / "x.tif")
    with rasterio.open(uit) as r:
        assert tuple(r.bounds) == bbox
        assert r.dtypes[0] == "float32" and r.nodata == NODATA
        a = r.read(1)
    assert set(np.unique(a)) == {1.0, 2.0}
    assert a[0, 0] == 1.0 and a[0, -1] == 2.0


def test_proxy_opties_only_when_unset():
    env = {"HTTPS_PROXY": "http://p:1", "REQUESTS_CA_BUNDLE": "/ca.pem"}
    o = gdal_proxy_opties(env)
    assert o["GDAL_HTTP_PROXY"] == "http://p:1"
    assert o["GDAL_CURL_CA_BUNDLE"] == "/ca.pem" and o["CURL_CA_BUNDLE"] == "/ca.pem"
    env.update(GDAL_HTTP_PROXY="x", GDAL_CURL_CA_BUNDLE="y", CURL_CA_BUNDLE="z")
    assert gdal_proxy_opties(env) == {}
