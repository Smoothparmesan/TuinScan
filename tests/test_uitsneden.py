import numpy as np
from PIL import Image

from miniwijk import rd_box
from tuinscan.rasters import bronnen_uit_cache
from tuinscan.uitsneden import maak_uitsnede, maak_uitsneden

R1_ACHTER = (0, 18, 6, 32)


def test_thumb_max_200px(miniwijk, tmp_path):
    bronnen = bronnen_uit_cache(miniwijk.mappen)
    pad = maak_uitsnede(bronnen["zomer_nu"], rd_box(R1_ACHTER), 5.0, 200, tmp_path / "t.jpg")
    assert pad is not None and pad.exists()
    with Image.open(pad) as img:
        assert max(img.size) == 200 and img.format == "JPEG" and img.mode == "RGB"


def test_outline_drawn(miniwijk, tmp_path):
    bronnen = bronnen_uit_cache(miniwijk.mappen)
    pad = maak_uitsnede(bronnen["zomer_nu"], rd_box(R1_ACHTER), 5.0, 800, tmp_path / "d.jpg")
    px = np.asarray(Image.open(pad).convert("RGB")).astype(int)
    geel = (abs(px - (255, 212, 0)).sum(axis=-1) < 60).sum()
    assert geel > 100


def test_missing_source_returns_none(miniwijk, tmp_path):
    assert maak_uitsnede(None, rd_box(R1_ACHTER), 5.0, 200, tmp_path / "x.jpg") is None
    assert (
        maak_uitsnede(tmp_path / "weg.tif", rd_box(R1_ACHTER), 5.0, 200, tmp_path / "x.jpg") is None
    )
    assert not (tmp_path / "x.jpg").exists()


def test_fully_outside_returns_none(miniwijk, tmp_path):
    bronnen = bronnen_uit_cache(miniwijk.mappen)
    ver = rd_box((1000, 1000, 1006, 1006))
    assert maak_uitsnede(bronnen["zomer_nu"], ver, 5.0, 200, tmp_path / "x.jpg") is None


def test_maak_uitsneden_names_and_sizes(miniwijk, tmp_path):
    bronnen = bronnen_uit_cache(miniwijk.mappen)
    bronnen["winter_vorig"] = None
    uit = maak_uitsneden("12345", rd_box(R1_ACHTER), bronnen, tmp_path)
    assert set(uit) == {
        "thumb_zomer_nu", "thumb_zomer_vorig", "detail_winter_nu",
        "detail_winter_vorig", "detail_zomer_nu", "detail_zomer_vorig",
    }  # fmt: skip
    assert uit["detail_winter_vorig"] is None
    assert uit["thumb_zomer_nu"] == tmp_path / "uitsneden" / "12345_thumb_zomer_nu.jpg"
    assert max(Image.open(uit["detail_winter_nu"]).size) == 800
    assert max(Image.open(uit["thumb_zomer_vorig"]).size) == 200
