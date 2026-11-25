import dataclasses
import logging

import geopandas as gpd
import numpy as np
import pytest
from rasterio.transform import from_origin

from miniwijk import VOORBEELD, rd_box
from tuinscan import signalen
from tuinscan.config import laad_config
from tuinscan.rasters import TuinRasters, bouw_tuinrasters, bronnen_uit_cache, overhang_zone
from tuinscan.signalen import (
    bereken_signalen,
    exg,
    ndvi_cir,
    s1_groentoename,
    s2_hoge_begroeiing,
    s3_overhang,
    s4a_hoog_object,
    s5_verharding,
    schaduw,
    vegetatie,
)
from tuinscan.stappen import stap_tuinen

CFG = laad_config(VOORBEELD)
RES = 0.25
A = RES**2
GROEN = (60, 140, 50)
GRIJS = (128, 128, 128)
ZWART = (0, 0, 0)
CIR_GROEN = (200, 60, 130)
CIR_GRIJS = (110, 120, 120)


def _beeld(kleur, n: int = 10, banden: int = 3) -> np.ma.MaskedArray:
    data = np.empty((banden, n, n), dtype=np.float32)
    data[:] = np.asarray(kleur, dtype=np.float32).reshape(banden, 1, 1)
    return np.ma.masked_array(data, mask=np.zeros_like(data, dtype=bool))


def _kleur(beeld, sel, kleur) -> np.ma.MaskedArray:
    for i, v in enumerate(kleur):
        beeld[(i, *sel)] = v
    return beeld


def _hoogte(n: int = 10, waarde: float = 0.0) -> np.ma.MaskedArray:
    return _beeld((waarde,), n, banden=1)


def _tr(n: int = 10, **kw) -> TuinRasters:
    velden = dict(
        transform=from_origin(120000, 485000 + n * RES, RES, RES), vorm=(n, n),
        tuin_masker=np.ones((n, n), dtype=bool), zone_masker=np.zeros((n, n), dtype=bool),
        zomer_nu=_beeld(GRIJS, n), zomer_vorig=None, cir_nu=None, cir_vorig=None,
        ndsm=None, ndsm_vorig=None,
    )  # fmt: skip
    velden.update(kw)
    return TuinRasters(**velden)


def test_exg_green_vs_grey():
    rgb = np.array([[[0, 100, 0]], [[255, 100, 0]], [[0, 100, 0]]], dtype=np.float32)
    e = exg(rgb)
    assert e[0, 0] > 0.5
    assert e[0, 1] == pytest.approx(0.0)
    assert e[0, 2] == 0.0  # S = 0 -> 0, geen deling door nul


def test_ndvi_band_order():
    cir = np.array([[[200]], [[50]], [[10]]], dtype=np.float32)
    assert ndvi_cir(cir)[0, 0] == pytest.approx(0.6)
    assert ndvi_cir(np.zeros((3, 1, 1)))[0, 0] == 0.0


def test_schaduw_threshold():
    rgb = np.array([[[30, 50]], [[30, 50]], [[30, 50]]], dtype=np.float32)
    assert schaduw(rgb, 40).tolist() == [[True, False]]


def test_vegetatie_ndvi_or_exg():
    zomer = _kleur(_beeld(GRIJS, 2), (0, slice(None)), GROEN)  # rij 0 groen in RGB
    cir = _kleur(_beeld(CIR_GRIJS, 2), (slice(None), 0), CIR_GROEN)  # kolom 0 groen in CIR
    assert vegetatie(zomer, cir, CFG).tolist() == [[True, True], [True, False]]
    assert vegetatie(zomer, None, CFG).tolist() == [[True, True], [False, False]]


def test_s1_increase():
    vorig = _kleur(_beeld(GRIJS), (slice(0, 2),), GROEN)
    nu = _kleur(_beeld(GRIJS), (slice(0, 5),), GROEN)
    cir_vorig = _kleur(_beeld(CIR_GRIJS), (slice(0, 2),), CIR_GROEN)
    cir_nu = _kleur(_beeld(CIR_GRIJS), (slice(0, 5),), CIR_GROEN)
    tr = _tr(zomer_nu=nu, zomer_vorig=vorig, cir_nu=cir_nu, cir_vorig=cir_vorig)
    assert s1_groentoename(tr, CFG) == pytest.approx(0.30, abs=0.01)


def test_s1_none_without_previous_year():
    assert s1_groentoename(_tr(), CFG) is None
    assert s1_groentoename(_tr(zomer_nu=None, zomer_vorig=_beeld(GRIJS)), CFG) is None


def test_s1_ndvi_only_when_both_years_have_cir():
    # NDVI ziet groen dat ExG mist; zonder CIR van vorig jaar mag dat geen toename geven
    cir_nu = _kleur(_beeld(CIR_GRIJS), (slice(0, 5),), CIR_GROEN)
    tr = _tr(zomer_vorig=_beeld(GRIJS), cir_nu=cir_nu)
    assert s1_groentoename(tr, CFG) == pytest.approx(0.0)
    tr = _tr(zomer_vorig=_beeld(GRIJS), cir_nu=cir_nu, cir_vorig=_beeld(CIR_GRIJS))
    assert s1_groentoename(tr, CFG) == pytest.approx(0.5)


def test_s2_share_over_2m():
    zomer = _kleur(_beeld(GRIJS), (slice(0, 5), slice(0, 10)), GROEN)  # helft groen
    ndsm = _hoogte()
    ndsm[0, 0:5, 0:5] = 3.0  # kwart: groen en 3 m
    ndsm[0, 0:5, 5:10] = 1.0  # kwart: groen maar laag
    ndsm[0, 5:10, 0:5] = 3.0  # grijs en hoog (geen begroeiing)
    assert s2_hoge_begroeiing(_tr(zomer_nu=zomer, ndsm=ndsm), CFG) == pytest.approx(0.25)


def test_s2_none_without_ndsm():
    assert s2_hoge_begroeiing(_tr(), CFG) is None


def test_s3_counts_only_zone():
    n = 10
    tuin = np.zeros((n, n), dtype=bool)
    tuin[:, :5] = True
    zone = np.zeros((n, n), dtype=bool)
    zone[:, 6:] = True
    zomer = _beeld(GROEN, n)
    ndsm = _hoogte(n, 3.0)  # alles groen en hoog, ook in de tuin
    ndsm[0, :, 6:] = 0.5  # zone laag ...
    ndsm[0, 0:2, 6:10] = 3.0  # ... behalve 8 px
    tr = _tr(n, tuin_masker=tuin, zone_masker=zone, zomer_nu=zomer, ndsm=ndsm)
    assert s3_overhang(tr, CFG) == pytest.approx(8 * A)  # 0,5 m²


def test_s3_empty_zone_zero_and_missing_ndsm_none():
    assert s3_overhang(_tr(ndsm=_hoogte()), CFG) == 0.0
    zone = np.ones((10, 10), dtype=bool)
    assert s3_overhang(_tr(zone_masker=zone), CFG) is None


def _blok(ndsm, r0, c0, h, b, z=2.0):
    ndsm[0, r0 : r0 + h, c0 : c0 + b] = z
    return ndsm


def test_s4a_small_object_ignored():
    n = 40
    klein = _blok(_hoogte(n), 2, 2, 4, 6)  # 24 px = 1,5 m²
    assert s4a_hoog_object(_tr(n, zomer_nu=_beeld(GRIJS, n), ndsm=klein), CFG, False) == 0.0
    groot = _blok(_hoogte(n), 2, 2, 8, 8)  # 64 px = 4 m²
    tr = _tr(n, zomer_nu=_beeld(GRIJS, n), ndsm=groot)
    assert s4a_hoog_object(tr, CFG, False) == pytest.approx(4.0)


def test_s4a_diagonal_neighbours_are_one_region():
    n = 40
    ndsm = _blok(_blok(_hoogte(n), 0, 0, 4, 4), 4, 4, 4, 4)  # 2 x 1 m², hoek aan hoek
    tr = _tr(n, zomer_nu=_beeld(GRIJS, n), ndsm=ndsm)
    assert s4a_hoog_object(tr, CFG, False) == pytest.approx(2.0)


def test_s4a_vegetation_and_outside_tuin_not_counted():
    n = 40
    ndsm = _blok(_blok(_hoogte(n), 0, 0, 8, 8), 20, 20, 8, 8)
    zomer = _kleur(_beeld(GRIJS, n), (slice(0, 8), slice(0, 8)), GROEN)  # blok 1 is groen
    tuin = np.ones((n, n), dtype=bool)
    tuin[20:, 20:] = False  # blok 2 buiten de tuin
    tr = _tr(n, tuin_masker=tuin, zomer_nu=zomer, ndsm=ndsm)
    assert s4a_hoog_object(tr, CFG, False) == 0.0


def test_s4a_existing_in_previous_ahn_ignored():
    n = 40
    ndsm = _blok(_hoogte(n), 2, 2, 8, 8)
    tr = _tr(n, zomer_nu=_beeld(GRIJS, n), ndsm=ndsm, ndsm_vorig=ndsm.copy())
    assert s4a_hoog_object(tr, CFG, False) == 0.0
    tr = _tr(n, zomer_nu=_beeld(GRIJS, n), ndsm=ndsm, ndsm_vorig=_hoogte(n))
    assert s4a_hoog_object(tr, CFG, False) == pytest.approx(4.0)


def test_s4a_standaardberging_ignored():
    n = 40
    zes = _blok(_hoogte(n), 2, 2, 8, 12)  # 96 px = 6 m²
    tr = _tr(n, zomer_nu=_beeld(GRIJS, n), ndsm=zes)
    assert s4a_hoog_object(tr, CFG, True) == 0.0
    assert s4a_hoog_object(tr, CFG, False) == pytest.approx(6.0)
    twee = _blok(zes.copy(), 20, 20, 8, 8)  # + 4 m²: alleen de grootste valt weg
    tr = _tr(n, zomer_nu=_beeld(GRIJS, n), ndsm=twee)
    assert s4a_hoog_object(tr, CFG, True) == pytest.approx(4.0)
    groot = _blok(_hoogte(n), 2, 2, 12, 16)  # 12 m² > standaardberging_max
    tr = _tr(n, zomer_nu=_beeld(GRIJS, n), ndsm=groot)
    assert s4a_hoog_object(tr, CFG, True) == pytest.approx(12.0)


def test_s4a_none_without_ndsm():
    assert s4a_hoog_object(_tr(), CFG, False) is None


def test_s5_both_years():
    nu = _kleur(_beeld(GRIJS), (slice(0, 2),), GROEN)  # 80 % grijs
    s5, toename = s5_verharding(_tr(zomer_nu=nu, zomer_vorig=nu.copy()), CFG)
    assert s5 == pytest.approx(0.8)
    assert toename == pytest.approx(0.0)
    vorig = _kleur(_beeld(GRIJS), (slice(0, 3),), GROEN)  # 70 % grijs
    s5, toename = s5_verharding(_tr(zomer_nu=nu, zomer_vorig=vorig), CFG)
    assert s5 == pytest.approx(0.7)
    assert toename == pytest.approx(0.1)


def test_s5_none_without_previous_year():
    assert s5_verharding(_tr(), CFG) == (None, None)


def test_shadow_excluded_from_denominator():
    zomer = _kleur(_beeld(GRIJS), (slice(0, 5),), ZWART)  # helft schaduw
    zomer = _kleur(zomer, (slice(5, 7),), GROEN)  # 2 van de 5 resterende rijen groen
    tr = _tr(zomer_nu=zomer, zomer_vorig=zomer.copy(), ndsm=_hoogte(waarde=3.0))
    assert s2_hoge_begroeiing(tr, CFG) == pytest.approx(0.4)
    assert s5_verharding(tr, CFG)[0] == pytest.approx(0.6)


def test_masked_pixels_excluded():
    zomer = _kleur(_beeld(GRIJS), (slice(0, 5),), GROEN)
    zomer[:, 0:5] = np.ma.masked  # het groene deel ontbreekt
    tr = _tr(zomer_nu=zomer, zomer_vorig=_beeld(GRIJS), ndsm=_hoogte(waarde=3.0))
    assert s2_hoge_begroeiing(tr, CFG) == pytest.approx(0.0)
    assert s5_verharding(tr, CFG)[0] == pytest.approx(1.0)


def test_all_masked_returns_none():
    def leeg(kleur):
        b = _beeld(kleur)
        b[:] = np.ma.masked
        return b

    tr = _tr(zomer_nu=leeg(GRIJS), zomer_vorig=leeg(GRIJS), cir_nu=leeg(CIR_GRIJS),
             cir_vorig=leeg(CIR_GRIJS), ndsm=_hoogte(), ndsm_vorig=_hoogte())  # fmt: skip
    s = bereken_signalen(tr, CFG, negeer_berging=False)
    assert s["S1"] is None and s["S2"] is None
    assert s["S5"] is None and s["S5_toename"] is None
    assert s["S4a"] is None


def test_bereken_signalen_keys_and_isolates_errors(monkeypatch, caplog):
    def kapot(tr, cfg):
        raise RuntimeError("stuk")

    monkeypatch.setattr(signalen, "s2_hoge_begroeiing", kapot)
    tr = _tr(zomer_vorig=_beeld(GRIJS), ndsm=_hoogte())
    with caplog.at_level(logging.ERROR):
        s = bereken_signalen(tr, CFG, negeer_berging=False)
    assert set(s) == {"S1", "S2", "S3", "S4a", "S5", "S5_toename"}
    assert s["S2"] is None and "S2" in caplog.text
    assert s["S1"] == pytest.approx(0.0) and s["S5"] == pytest.approx(1.0)
    assert s["S3"] == 0.0 and s["S4a"] == 0.0


def test_signals_on_miniwijk(miniwijk):
    mw = miniwijk
    cfg = mw.cfg
    s = cfg.signalen
    cfg = dataclasses.replace(cfg, tuin=dataclasses.replace(cfg.tuin, delen=["voor", "achter"]))
    stap_tuinen(cfg, mw.mappen)
    tuinen = gpd.read_file(mw.mappen.ronde_dir / "tuinen.gpkg", layer="tuinvlak")
    bronnen = bronnen_uit_cache(mw.mappen)
    voetpad = mw.wegdelen[mw.wegdelen["functie"] == "voetpad"]

    def tuin(huis, deel):
        rij = tuinen[(tuinen["vhe_nummer"] == mw.vhe_nummers[huis]) & (tuinen["deel"] == deel)]
        assert len(rij) == 1
        return rij.geometry.iloc[0]

    def rasters(huis, deel):
        g = tuin(huis, deel)
        zone = overhang_zone(g, mw.panden, voetpad, mw.begroeid, s.s3_buffer_m)
        return bouw_tuinrasters(g, zone, bronnen, s.resolutie_m)

    r1 = bereken_signalen(rasters("R1", "achter"), cfg, negeer_berging=False)
    r1_m2 = tuin("R1", "achter").area
    assert r1["S1"] == pytest.approx(20 / r1_m2, abs=0.05)  # groenvlak 20 -> 40 m²
    assert r1["S2"] == pytest.approx(0.0)  # 1 m hoog: onder s2_hoogte_m
    assert r1["S4a"] == pytest.approx(0.0)

    r2 = bereken_signalen(rasters("R2", "voor"), cfg, negeer_berging=False)
    assert r2["S3"] == pytest.approx(5.0, abs=0.5)  # haag 5 x 1 m over het voetpad
    assert r2["S2"] == pytest.approx(5 / tuin("R2", "voor").area, abs=0.02)  # haag in tuin

    p2 = bereken_signalen(rasters("V", "achter"), cfg, negeer_berging=False)
    assert p2["S4a"] == pytest.approx(4.0, abs=0.5)  # blok 2 x 2 m, 2 m hoog
    assert bereken_signalen(rasters("V", "achter"), cfg, negeer_berging=True)["S4a"] == 0.0
    assert p2["S5_toename"] == pytest.approx(0.0)  # blok vervangt verharding: beide niet groen
    assert r1["S5_toename"] < 0  # groenvlak groeit
    assert rd_box((22, 22, 24, 24)).within(tuin("V", "achter"))
