import pytest

from tuinscan.cli import main
from tuinscan.kalibreer import KLASSEN, kalibreer


def test_kalibreer_percentiles_per_layer_and_class(miniwijk, capsys):
    mw = miniwijk
    uit = kalibreer(mw.cfg, mw.mappen)
    assert set(uit.lagen) == {"zomer_nu", "zomer_vorig", "cir_nu", "cir_vorig"}
    cir = uit.lagen["cir_nu"]
    assert cir.index_naam == "NDVI" and set(cir.klassen) == set(KLASSEN)
    # miniwijk: verharding NDVI -0,04, dak -0,25, gras (openbaar groen) 0,38
    assert cir.klassen["rijbaan"].p[50] == pytest.approx(-0.04, abs=0.01)
    assert cir.klassen["dak"].p[95] < 0
    assert cir.klassen["begroeid"].p[25] == pytest.approx(0.38, abs=0.01)
    assert cir.klassen["begroeid"].groen == pytest.approx(1.0)
    assert cir.klassen["rijbaan"].groen == 0.0 and cir.klassen["dak"].n > 0
    assert -0.04 < cir.voorstel < 0.38
    zomer = uit.lagen["zomer_nu"]
    assert zomer.index_naam == "ExG" and zomer.klassen["begroeid"].p[50] > 0.12
    # gecombineerd masker per jaar (NDVI waar CIR geldig is)
    assert uit.masker["nu"]["begroeid"] == pytest.approx(1.0)
    assert uit.masker["nu"]["dak"] == 0.0
    out = capsys.readouterr().out
    assert "luchtfoto.lagen.cir_nu.ndvi_drempel" in out and "p95" in out


def test_kalibreer_skips_missing_layer(miniwijk, capsys):
    mw = miniwijk
    (mw.mappen.cache / "raster" / "cir_vorig.tif").unlink()
    uit = kalibreer(mw.cfg, mw.mappen)
    assert "cir_vorig" not in uit.lagen and "cir_nu" in uit.lagen
    assert "cir_vorig: ontbreekt" in capsys.readouterr().out


def test_kalibreer_cli(miniwijk, capsys):
    assert main(["kalibreer", "--config", str(miniwijk.config_pad)]) == 0
    assert "ndvi_drempel" in capsys.readouterr().out
