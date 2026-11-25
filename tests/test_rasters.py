import json

import numpy as np
import pytest
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin, rowcol

from miniwijk import (
    AHN_NODATA,
    BLOK_P2,
    GROENVLAK_R1,
    HAAG_R2,
    HOOGTE,
    KLEUR_CIR,
    KLEUR_RGB,
    OORSPRONG,
    rd,
    rd_box,
)
from tuinscan.rasters import (
    bouw_tuinrasters,
    bronnen_uit_cache,
    lees_venster,
    overhang_zone,
)

RES = 0.25
R1_ACHTER = (0, 18, 6, 32)
R2_VOOR = (6, 2, 12, 8)


def _pixel(tr, x, y) -> tuple[int, int]:
    """Rij/kolom van relatieve fixture-coördinaat in het TuinRasters-raster."""
    return rowcol(tr.transform, *rd(x, y))


def _schrijf(pad, data, transform, nodata=None, mask=None):
    data = np.asarray(data)
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        with rasterio.open(
            pad, "w", driver="GTiff", width=data.shape[2], height=data.shape[1],
            count=data.shape[0], dtype=data.dtype, crs="EPSG:28992", transform=transform,
            nodata=nodata,
        ) as dst:  # fmt: skip
            dst.write(data)
            if mask is not None:
                dst.write_mask(mask)
    return pad


def test_bronnen_uit_cache(miniwijk):
    bronnen = bronnen_uit_cache(miniwijk.mappen)
    for k in ("zomer_nu", "zomer_vorig", "cir_nu", "cir_vorig", "winter_nu", "winter_vorig",
              "ahn_dtm", "ahn_dsm"):  # fmt: skip
        assert bronnen[k] == miniwijk.rasters[k]
    assert bronnen["ahn_vorig_dtm"] is None and bronnen["ahn_vorig_dsm"] is None


def test_bronnen_uit_cache_leeg_and_missing(miniwijk):
    manifest = miniwijk.mappen.cache / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["cir_vorig"]["status"] = "leeg"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    miniwijk.rasters["winter_vorig"].unlink()
    bronnen = bronnen_uit_cache(miniwijk.mappen)
    assert bronnen["cir_vorig"] is None
    assert bronnen["winter_vorig"] is None
    assert bronnen["cir_nu"] is not None


def test_window_aligned_to_grid(miniwijk):
    tuin = rd_box(R1_ACHTER)
    tr = bouw_tuinrasters(tuin, None, bronnen_uit_cache(miniwijk.mappen), RES)
    assert tr.transform.a == RES and tr.transform.e == -RES
    assert tr.transform.c % RES == 0 and tr.transform.f % RES == 0
    # venster = bounds + 1 m
    assert tr.transform.c == OORSPRONG[0] - 1 and tr.transform.f == OORSPRONG[1] + 33
    assert tr.vorm == (int(16 / RES), int(8 / RES))
    assert tr.tuin_masker.shape == tr.vorm and tr.tuin_masker.dtype == bool
    assert tr.tuin_masker.sum() * RES**2 == pytest.approx(tuin.area)
    assert not tr.zone_masker.any()
    for r in (tr.zomer_nu, tr.zomer_vorig, tr.cir_nu, tr.cir_vorig):
        assert isinstance(r, np.ma.MaskedArray) and r.shape == (3, *tr.vorm)
        assert not np.ma.getmaskarray(r).any()
    assert tr.ndsm.shape == (1, *tr.vorm)
    assert tr.ndsm_vorig is None  # fixture heeft geen vorige AHN


def test_values_resampled_from_sources(miniwijk):
    tr = bouw_tuinrasters(rd_box(R1_ACHTER), None, bronnen_uit_cache(miniwijk.mappen), RES)
    groen = _pixel(tr, 3, 27)  # midden in GROENVLAK_R1 (alleen nu groen)
    kaal = _pixel(tr, 3, 31)  # verharding
    assert GROENVLAK_R1[1] < 27 < GROENVLAK_R1[3]
    np.testing.assert_allclose(tr.zomer_nu[:, groen[0], groen[1]], KLEUR_RGB["groen"], atol=1)
    np.testing.assert_allclose(tr.zomer_vorig[:, groen[0], groen[1]], KLEUR_RGB["verharding"],
                               atol=1)  # fmt: skip
    np.testing.assert_allclose(tr.cir_nu[:, groen[0], groen[1]], KLEUR_CIR["groen"], atol=1)
    np.testing.assert_allclose(tr.zomer_nu[:, kaal[0], kaal[1]], KLEUR_RGB["verharding"], atol=1)
    assert tr.ndsm[0, groen[0], groen[1]] == pytest.approx(HOOGTE["groenvlak"])
    assert tr.ndsm[0, kaal[0], kaal[1]] == pytest.approx(0.0)


def test_missing_source_is_none(miniwijk):
    bronnen = bronnen_uit_cache(miniwijk.mappen)
    bronnen["zomer_vorig"] = None
    tr = bouw_tuinrasters(rd_box(R1_ACHTER), None, bronnen, RES)
    assert tr.zomer_vorig is None
    assert tr.zomer_nu is not None
    bronnen["ahn_dtm"] = None
    assert bouw_tuinrasters(rd_box(R1_ACHTER), None, bronnen, RES).ndsm is None


def test_lees_venster_none_for_missing_path(tmp_path):
    bounds = (*rd(0, 0), *rd(5, 5))
    assert lees_venster(None, bounds, RES, Resampling.bilinear) is None
    assert lees_venster(tmp_path / "bestaat_niet.tif", bounds, RES, Resampling.bilinear) is None


def test_outside_coverage_masked(miniwijk):
    with rasterio.open(miniwijk.rasters["zomer_nu"]) as d:
        rand = d.bounds.right - OORSPRONG[0]
    tuin = rd_box((rand - 4, 10, rand + 4, 20))
    tr = bouw_tuinrasters(tuin, None, bronnen_uit_cache(miniwijk.mappen), RES)
    in_tuin = np.ma.getmaskarray(tr.zomer_nu)[0][tr.tuin_masker]
    assert in_tuin.mean() == pytest.approx(0.5, abs=0.05)
    ndsm_masker = np.ma.getmaskarray(tr.ndsm)[0][tr.tuin_masker]
    assert ndsm_masker.mean() == pytest.approx(0.5, abs=0.05)


def test_window_fully_outside_source_all_masked(miniwijk):
    bounds = (*rd(500, 500), *rd(505, 505))
    r = lees_venster(miniwijk.rasters["zomer_nu"], bounds, RES, Resampling.bilinear)
    assert r.shape == (3, 20, 20) and np.ma.getmaskarray(r).all()


def test_internal_mask_honoured(tmp_path):
    # WMTS-mozaïek met een mislukte tegel (interne mask 0, data 0) in de linkerhelft
    x0, y1, res = OORSPRONG[0] - 0.03, OORSPRONG[1] + 10.02, 0.21
    data = np.full((3, 48, 48), 100, dtype=np.uint8)
    mask = np.full((48, 48), 255, dtype=np.uint8)
    data[:, :, :24], mask[:, :24] = 0, 0
    data[:, 40:, 40:] = 0  # echt zwarte pixels binnen een geldige tegel blijven geldig
    pad = _schrijf(tmp_path / "mozaiek.tif", data, from_origin(x0, y1, res, res), 0, mask)
    bounds = (*rd(0, 0), *rd(10, 10))
    r = lees_venster(pad, bounds, RES, Resampling.bilinear)
    m = np.ma.getmaskarray(r)[0]
    links = slice(0, int((x0 + 24 * res - OORSPRONG[0]) / RES) - 1)
    assert m[:, links].all()
    assert not m[:30, 26:].any()
    assert r[0, 2:30, 26:].min() == pytest.approx(100, abs=0.5)
    assert not m[-2:, -2:].any()  # zwart maar binnen mask: geldig


def test_ahn_nodata_masked(tmp_path):
    data = np.full((1, 20, 20), 1.0, dtype=np.float32)
    data[0, :10] = AHN_NODATA
    pad = _schrijf(tmp_path / "dsm.tif", data, from_origin(*rd(0, 10), 0.5, 0.5), AHN_NODATA)
    r = lees_venster(pad, (*rd(0, 0), *rd(10, 10)), RES, Resampling.nearest)
    m = np.ma.getmaskarray(r)[0]
    assert m[:20].all() and not m[20:].any()
    assert float(r[0, 20:].max()) == pytest.approx(1.0)


def test_ndsm_fills_dtm_gap(tmp_path):
    t = from_origin(*rd(-20, 30), 0.5, 0.5)  # 100 x 100 px, 50 x 50 m
    dtm = np.full((1, 100, 100), 1.0, dtype=np.float32)
    dtm[0, 35:65, 35:65] = AHN_NODATA  # gat van 30 x 30 px (15 x 15 m) onder een gebouw
    dsm = np.full((1, 100, 100), 3.0, dtype=np.float32)
    bronnen = {
        "ahn_dtm": _schrijf(tmp_path / "dtm.tif", dtm, t, AHN_NODATA),
        "ahn_dsm": _schrijf(tmp_path / "dsm.tif", dsm, t, AHN_NODATA),
    }
    tuin = rd_box((-2.5, -2.5, 12.5, 12.5))  # precies het gat
    tr = bouw_tuinrasters(tuin, None, bronnen, RES)
    assert tr.zomer_nu is None
    nd = tr.ndsm[0][tr.tuin_masker]
    assert not np.ma.getmaskarray(nd).any()
    np.testing.assert_allclose(nd.filled(np.nan), 2.0, atol=1e-5)


def test_ndsm_under_pand_from_filled_dtm(miniwijk):
    tuin = rd_box((0, 8, 18, 18))  # pand `rij`: DTM nodata, 36 x 20 px
    tr = bouw_tuinrasters(tuin, None, bronnen_uit_cache(miniwijk.mappen), RES)
    nd = tr.ndsm[0][tr.tuin_masker]
    assert not np.ma.getmaskarray(nd).any()
    assert float(np.ma.median(nd)) == pytest.approx(HOOGTE["rij"])


def test_overhang_zone_only_public(miniwijk):
    mw = miniwijk
    voetpad = mw.wegdelen[mw.wegdelen["functie"] == "voetpad"]
    publiek = voetpad.union_all().union(mw.begroeid.union_all())
    tuin = rd_box(R2_VOOR)
    zone = overhang_zone(tuin, mw.panden, voetpad, mw.begroeid, 1.5)
    assert zone is not None and zone.area > 6 * 1.5
    assert zone.difference(publiek).area < 1e-6
    assert zone.intersection(tuin).area < 1e-9
    assert zone.intersection(mw.panden.union_all()).area < 1e-9
    # haag over het voetpad ligt in de zone
    assert zone.contains(rd_box((HAAG_R2[0], 1, HAAG_R2[2], 2)))


def test_overhang_zone_none_without_public(miniwijk):
    mw = miniwijk
    voetpad = mw.wegdelen[mw.wegdelen["functie"] == "voetpad"]
    assert overhang_zone(rd_box(R1_ACHTER), mw.panden, voetpad, mw.begroeid, 1.5) is None
    assert overhang_zone(rd_box(R2_VOOR), mw.panden, None, None, 1.5) is None


def test_overhang_zone_public_green(miniwijk):
    mw = miniwijk
    voetpad = mw.wegdelen[mw.wegdelen["functie"] == "voetpad"]
    tuin = rd_box((29, 18, 34, 32))  # oostelijk deel achtertuin P2, grenst aan openbaar groen
    zone = overhang_zone(tuin, mw.panden, voetpad, mw.begroeid, 1.5)
    assert zone.difference(mw.begroeid.union_all()).area < 1e-6
    assert zone.area == pytest.approx(14 * 1.5 + np.pi * 1.5**2 / 4, rel=0.01)


def test_zone_masker_in_window(miniwijk):
    mw = miniwijk
    voetpad = mw.wegdelen[mw.wegdelen["functie"] == "voetpad"]
    tuin = rd_box(R2_VOOR)
    zone = overhang_zone(tuin, mw.panden, voetpad, mw.begroeid, 1.5)
    tr = bouw_tuinrasters(tuin, zone, bronnen_uit_cache(mw.mappen), RES)
    # venster omvat tuin ∪ zone + 1 m
    x0, _, _, y1 = zone.union(tuin).bounds
    assert tr.transform.c <= x0 - 1 and tr.transform.f >= y1 + 1
    assert not (tr.zone_masker & tr.tuin_masker).any()
    assert tr.zone_masker.sum() * RES**2 == pytest.approx(zone.area, rel=0.05)
    # haag (2,5 m) over het voetpad: 5 x 1 m in de zone
    haag = tr.zone_masker & (tr.ndsm[0].filled(0) > 2.0)
    assert haag.sum() * RES**2 == pytest.approx(5.0)


def test_blok_in_p2_back_garden(miniwijk):
    tuin = rd_box((18, 18, 34, 32)).difference(rd_box((29, 26, 32, 29)))
    tr = bouw_tuinrasters(tuin, None, bronnen_uit_cache(miniwijk.mappen), RES)
    blok = tr.tuin_masker & (np.abs(tr.ndsm[0].filled(0) - HOOGTE["blok"]) < 0.01)
    assert blok.sum() * RES**2 == pytest.approx(
        (BLOK_P2[2] - BLOK_P2[0]) * (BLOK_P2[3] - BLOK_P2[1])
    )
