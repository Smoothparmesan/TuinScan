import io
import logging
from pathlib import Path

import numpy as np
import pytest
import rasterio
import requests
import responses
from PIL import Image

from tuinscan.pdok import wmts
from tuinscan.pdok.wmts import (
    TileMatrix,
    download_mozaiek,
    lees_capabilities,
    lijst_lagen,
    tegels_voor_bbox,
)

CAPS = Path(__file__).parent / "data" / "wmts_caps.xml"
URL = "https://example.test/wmts"
OX, OY = -285401.92, 903401.92


def matrix(res=0.21):
    return TileMatrix("14", res, OX, OY, 256, 256, 16384, 16384)


def jpeg(kleur):
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), kleur).save(buf, "JPEG", quality=95)
    return buf.getvalue()


def jpeg_ruis(laag, hoog, seed=0):
    arr = np.random.default_rng(seed).integers(laag, hoog + 1, (256, 256, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG", quality=95)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def geen_slaap(monkeypatch):
    monkeypatch.setattr(wmts, "_slaap", lambda s: None)


def test_resolution_from_scale():
    caps = lees_capabilities(CAPS.read_bytes(), "EPSG:28992")
    m = caps.matrices["14"]
    assert m.resolutie == pytest.approx(0.21)
    assert (m.origin_x, m.origin_y) == (-285401.92, 903401.92)
    assert (m.tegel_b, m.tegel_h, m.matrix_b, m.matrix_h) == (256, 256, 16384, 16384)
    assert set(caps.matrices) == {"14", "15"}  # EPSG:3857-set genegeerd
    assert caps.lagen == {"2025_ortho25": "image/jpeg", "2024_ortho25": "image/jpeg"}


def test_tiles_cover_bbox():
    m = matrix()
    tw = 256 * 0.21
    x0 = OX + 100 * tw
    y0 = OY - 200 * tw
    bbox = (x0 + 1, y0 - 3 * tw + 1, x0 + 2 * tw - 1, y0 - 1)
    assert tegels_voor_bbox(m, bbox) == [
        (100, 200), (101, 200), (100, 201), (101, 201), (100, 202), (101, 202),
    ]


def test_tiles_clipped_to_matrix():
    m = TileMatrix("0", 0.21, OX, OY, 256, 256, 2, 2)
    assert tegels_voor_bbox(m, (OX - 500, OY - 500, OX + 1e6, OY + 1e6)) == [
        (0, 0), (1, 0), (0, 1), (1, 1),
    ]


def _mock(kleuren, status=None, bodies=None):
    for (c, r), k in kleuren.items():
        st = (status or {}).get((c, r), 200)
        body = (bodies or {}).get((c, r)) or (jpeg(k) if st == 200 else b"nope")
        responses.add(
            responses.GET,
            f"{URL}/2025_ortho25/EPSG:28992/14/{c}/{r}.jpeg",
            body=body,
            status=st,
            content_type="image/jpeg",
        )


def _bbox_2x2():
    tw = 256 * 0.21
    x0, y0 = OX + 100 * tw, OY - 200 * tw
    return (x0 + 1, y0 - 2 * tw + 1, x0 + 2 * tw - 1, y0 - 1)


KLEUREN = {
    (100, 200): (200, 30, 30),
    (101, 200): (30, 200, 30),
    (100, 201): (30, 30, 200),
    (101, 201): (120, 120, 60),
}


@responses.activate
def test_mosaic_georeference(tmp_path):
    _mock(KLEUREN)
    info = download_mozaiek(
        URL, "2025_ortho25", "EPSG:28992", matrix(), "image/jpeg", _bbox_2x2(),
        tmp_path / "m.tif", requests.Session(), 2,
    )
    assert (info.tegels_totaal, info.tegels_mislukt) == (4, 0)
    tw = 256 * 0.21
    with rasterio.open(info.pad) as ds:
        assert ds.crs.to_epsg() == 28992
        assert ds.count == 3 and ds.dtypes[0] == "uint8"
        assert ds.nodata == 0 and ds.profile["tiled"]
        assert ds.profile["compress"].lower() == "deflate"
        b = ds.bounds
        assert (b.left, b.top) == pytest.approx((OX + 100 * tw, OY - 200 * tw))
        assert (b.right, b.bottom) == pytest.approx((OX + 102 * tw, OY - 202 * tw))
        assert ds.width == 512 and ds.height == 512
        px = ds.read()[:, 128, 256 + 128]  # tegel (101,200) midden
        assert all(abs(int(a) - e) <= 6 for a, e in zip(px, KLEUREN[(101, 200)]))
        assert ds.read_masks(1).min() == 255


@responses.activate
def test_failed_tile_is_masked(tmp_path):
    _mock(KLEUREN, status={(100, 201): 404})
    info = download_mozaiek(
        URL, "2025_ortho25", "EPSG:28992", matrix(), "image/jpeg", _bbox_2x2(),
        tmp_path / "m.tif", requests.Session(), 2,
    )
    assert info.tegels_mislukt == 1
    with rasterio.open(info.pad) as ds:
        mask = ds.read_masks(1)
        assert (mask[256:, :256] == 0).all()
        assert (mask[:256, :] == 255).all() and (mask[256:, 256:] == 255).all()
        assert (ds.read()[:, 256:, :256] == 0).all()


@responses.activate
@pytest.mark.parametrize("kleur", [(255, 255, 255), (0, 0, 0)])
def test_white_or_black_tile_is_masked(tmp_path, kleur):
    k = dict(KLEUREN)
    k[(101, 201)] = kleur
    _mock(k)
    info = download_mozaiek(
        URL, "2025_ortho25", "EPSG:28992", matrix(), "image/jpeg", _bbox_2x2(),
        tmp_path / "m.tif", requests.Session(), 2,
    )
    assert info.tegels_mislukt == 1
    with rasterio.open(info.pad) as ds:
        assert (ds.read_masks(1)[256:, 256:] == 0).all()
        assert (ds.read_masks(1)[:256, :] == 255).all()


@responses.activate
def test_lijst_lagen():
    responses.add(responses.GET, URL, body=CAPS.read_bytes(), content_type="text/xml")
    assert lijst_lagen(URL) == ["2025_ortho25", "2024_ortho25"]


def _run(tmp_path, formaat="image/jpeg"):
    return download_mozaiek(
        URL, "2025_ortho25", "EPSG:28992", matrix(), formaat, _bbox_2x2(),
        tmp_path / "m.tif", requests.Session(), 2,
    )


@responses.activate
def test_near_white_noisy_tile_is_masked(tmp_path):
    _mock(KLEUREN, bodies={(101, 201): jpeg_ruis(252, 255)})
    info = _run(tmp_path)
    assert info.tegels_mislukt == 1
    with rasterio.open(info.pad) as ds:
        assert (ds.read_masks(1)[256:, 256:] == 0).all()


@responses.activate
def test_non_image_body_is_masked(tmp_path):
    _mock(KLEUREN, bodies={(100, 200): b"<html>fout</html>"})
    assert _run(tmp_path).tegels_mislukt == 1


@responses.activate
def test_all_failed_no_exception(tmp_path):
    _mock(KLEUREN, status={k: 404 for k in KLEUREN})
    info = _run(tmp_path)
    assert info.tegels_mislukt == info.tegels_totaal == 4
    with rasterio.open(info.pad) as ds:
        assert ds.read_masks(1).max() == 0


@responses.activate
def test_transient_error_is_retried(tmp_path):
    url = f"{URL}/2025_ortho25/EPSG:28992/14/100/200.jpeg"
    responses.add(responses.GET, url, status=503)
    responses.add(responses.GET, url, body=requests.ConnectionError("x"))
    responses.add(responses.GET, url, body=jpeg((200, 30, 30)), content_type="image/jpeg")
    _mock({k: v for k, v in KLEUREN.items() if k != (100, 200)})
    assert _run(tmp_path).tegels_mislukt == 0


@responses.activate
def test_persistent_failure_retries_then_logs(tmp_path, caplog):
    url = f"{URL}/2025_ortho25/EPSG:28992/14/100/200.jpeg"
    for _ in range(3):
        responses.add(responses.GET, url, status=500)
    _mock({k: v for k, v in KLEUREN.items() if k != (100, 200)})
    with caplog.at_level(logging.WARNING):
        assert _run(tmp_path).tegels_mislukt == 1
    assert sum(c.request.url == url for c in responses.calls) == 3
    assert "col=100 row=200" in caplog.text and "HTTP 500" in caplog.text


@responses.activate
def test_404_not_retried(tmp_path):
    _mock(KLEUREN, status={(100, 200): 404})
    _run(tmp_path)
    assert sum(c.request.url.endswith("/100/200.jpeg") for c in responses.calls) == 1


@responses.activate
def test_png_extension_from_formaat(tmp_path):
    for (c, r), k in KLEUREN.items():
        buf = io.BytesIO()
        Image.new("RGB", (256, 256), k).save(buf, "PNG")
        responses.add(
            responses.GET, f"{URL}/2025_ortho25/EPSG:28992/14/{c}/{r}.png",
            body=buf.getvalue(), content_type="image/png",
        )
    assert _run(tmp_path, "image/png").tegels_mislukt == 0
