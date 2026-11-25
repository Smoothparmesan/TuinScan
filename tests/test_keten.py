import shutil

import pandas as pd
import pytest

from tuinscan.cli import main
from tuinscan.stappen import (
    StapFout,
    stap_opruimen,
    stap_rapport,
    stap_score,
    stap_terugkoppeling_maak,
    stap_tuinen,
)


def test_keten_end_to_end(miniwijk):
    mw = miniwijk
    stap_tuinen(mw.cfg, mw.mappen)
    stap_score(mw.cfg, mw.mappen)
    stap_rapport(mw.cfg, mw.mappen)
    stap_terugkoppeling_maak(mw.cfg, mw.mappen)
    rd = mw.mappen.ronde_dir
    for naam in ("vhe.parquet", "tuinen.gpkg", "signalen.parquet", "scores.parquet",
                 "foutenlijst.csv", "rapport.html", "terugkoppeling.xlsx"):  # fmt: skip
        assert (rd / naam).exists(), naam
    assert len(pd.read_parquet(rd / "scores.parquet")) == 4


def _vul(mw):
    stap_tuinen(mw.cfg, mw.mappen)
    (mw.mappen.invoer / "tuincorrecties.gpkg").write_bytes(b"x")
    (mw.mappen.invoer / "voorbeeld_export.xlsx").write_bytes(b"x")
    return mw.mappen.invoer, mw.mappen.cache, mw.mappen.ronde_dir


def test_opruimen_requires_name(miniwijk):
    mw = miniwijk
    invoer, cache, ronde = _vul(mw)
    with pytest.raises(StapFout):
        stap_opruimen(mw.cfg, mw.mappen, "verkeerd")
    assert ronde.exists() and any(cache.iterdir()) and (invoer / mw.cfg.tobias.bestand).exists()
    stap_opruimen(mw.cfg, mw.mappen, ronde.name)
    assert not ronde.exists() and not any(cache.iterdir())  # alleen tuinscan-bestanden
    assert not (invoer / mw.cfg.tobias.bestand).exists()
    assert not (invoer / "voorbeeld_export.xlsx").exists()
    assert (invoer / "kolommen.yaml").exists()
    assert not (invoer / "tuincorrecties.gpkg").exists()


def test_opruimen_bewaart_correcties(miniwijk):
    mw = miniwijk
    invoer, _, ronde = _vul(mw)
    stap_opruimen(mw.cfg, mw.mappen, ronde.name, bewaar_correcties=True)
    assert (invoer / "tuincorrecties.gpkg").exists() and (invoer / "kolommen.yaml").exists()


def test_opruimen_cli_vraagt_naam(miniwijk, monkeypatch):
    mw = miniwijk
    _, cache, ronde = _vul(mw)
    monkeypatch.setattr("builtins.input", lambda _="": "nee")
    assert main(["opruimen", "--config", str(mw.config_pad)]) == 1
    assert ronde.exists()
    monkeypatch.setattr("builtins.input", lambda _="": ronde.name)
    assert main(["opruimen", "--config", str(mw.config_pad)]) == 0
    assert not ronde.exists()


def test_opruimen_controleert_alles_voor_verwijderen(miniwijk, tmp_path):
    from tuinscan.stappen import _controleer_doel

    mw = miniwijk
    buiten = tmp_path / "elders.txt"
    buiten.write_text("x")
    with pytest.raises(StapFout):
        _controleer_doel(buiten, mw.mappen.alle())
    with pytest.raises(StapFout):  # een databoom zelf ook niet
        _controleer_doel(mw.mappen.cache, mw.mappen.alle())
    assert buiten.exists() and mw.mappen.cache.exists()


def test_opruimen_symlink_verwijdert_alleen_link(miniwijk, tmp_path):
    mw = miniwijk
    _, cache, ronde = _vul(mw)
    doel = tmp_path / "buitenmap"
    doel.mkdir()
    (doel / "belangrijk.txt").write_text("x")
    shutil.rmtree(cache / "vector")
    try:
        (cache / "vector").symlink_to(doel, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks niet toegestaan")
    stap_opruimen(mw.cfg, mw.mappen, ronde.name)
    assert not (cache / "vector").is_symlink() and (doel / "belangrijk.txt").exists()


def test_opruimen_weigert_zonder_iets_te_verwijderen(miniwijk, monkeypatch, tmp_path):
    """Eén ongeldig doel (hier: ronde-map buiten uitvoer) -> niets weg."""
    import dataclasses

    mw = miniwijk
    invoer, cache, ronde = _vul(mw)
    elders = tmp_path / "elders"
    elders.mkdir()
    mappen = dataclasses.replace(mw.mappen, ronde_dir=elders)
    with pytest.raises(StapFout):
        stap_opruimen(mw.cfg, mappen, "elders")
    assert elders.exists() and ronde.exists() and any(cache.iterdir())


def test_opruimen_cli_eof(miniwijk, monkeypatch, capsys):
    mw = miniwijk
    _, _, ronde = _vul(mw)

    def eof(_=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert main(["opruimen", "--config", str(mw.config_pad)]) == 1
    assert ronde.exists() and "Fout" in capsys.readouterr().err


def test_alles_stopt_bij_stapfout(miniwijk, monkeypatch, capsys):
    mw = miniwijk
    shutil.rmtree(mw.mappen.cache / "vector")
    monkeypatch.setattr(
        "tuinscan.download.voer_download_uit",
        lambda *a: type("R", (), {"opgehaald": [], "overgeslagen": [], "mislukt": {}})(),
    )
    assert main(["alles", "--config", str(mw.config_pad)]) == 1
    assert "[download] klaar" in capsys.readouterr().out


def test_alles_telt_tijd(miniwijk, monkeypatch, capsys):
    mw = miniwijk
    monkeypatch.setattr(
        "tuinscan.download.voer_download_uit",
        lambda *a: type("R", (), {"opgehaald": [], "overgeslagen": ["x"], "mislukt": {}})(),
    )
    assert main(["alles", "--config", str(mw.config_pad)]) == 0
    out = capsys.readouterr().out
    for stap in ("download", "tuinen", "score", "rapport", "terugkoppeling maak"):
        assert f"[{stap}] klaar" in out
    assert "Alles klaar" in out


def test_opruimen_laat_vreemde_bestanden_in_cache(miniwijk):
    mw = miniwijk
    _, cache, ronde = _vul(mw)
    (cache / "manifest.json.tmp").write_text("x")
    (cache / "notities.txt").write_text("van de gebruiker")
    (cache / "ander_project").mkdir()
    stap_opruimen(mw.cfg, mw.mappen, ronde.name)
    assert sorted(p.name for p in cache.iterdir()) == ["ander_project", "notities.txt"]
    assert not ronde.exists()


def test_opruimen_weigert_ronde_map_zonder_kenmerk(miniwijk):
    mw = miniwijk
    _, cache, _ = _vul(mw)
    vreemd = mw.mappen.uitvoer / "vreemd"
    vreemd.mkdir()
    (vreemd / "rapportage.docx").write_text("x")
    mappen = mw.mappen.__class__(mw.mappen.invoer, cache, mw.mappen.uitvoer, vreemd)
    with pytest.raises(StapFout, match="ronde"):
        stap_opruimen(mw.cfg, mappen, "vreemd")
    assert (vreemd / "rapportage.docx").exists() and (cache / "manifest.json").exists()


def test_cli_schrijft_logbestand_zonder_adressen(miniwijk, capsys):
    import logging

    mw = miniwijk
    assert main(["tuinen", "--config", str(mw.config_pad)]) == 0
    assert main(["score", "--config", str(mw.config_pad)]) == 0
    log = (mw.mappen.ronde_dir / "tuinscan.log").read_text(encoding="utf-8")
    assert "INFO" in log and "score" in log
    assert "Tuinstraat" not in log  # geen adressen in het logbestand
    eigen = [h for h in logging.getLogger("tuinscan").handlers if getattr(h, "_tuinscan", False)]
    assert eigen == []  # handlers na afloop opgeruimd (bestand niet open op Windows)


def test_cli_waarschuwing_kort_op_console(miniwijk, monkeypatch, capsys):
    import logging

    def stap(cfg, mappen):
        logging.getLogger("tuinscan.stappen").warning("let op: %s", "iets")
        logging.getLogger("tuinscan.stappen").info("alleen in het log")

    monkeypatch.setattr("tuinscan.stappen.stap_rapport", stap)
    assert main(["rapport", "--config", str(miniwijk.config_pad)]) == 0
    err = capsys.readouterr().err
    assert "let op: iets" in err and "alleen in het log" not in err
    log = (miniwijk.mappen.ronde_dir / "tuinscan.log").read_text(encoding="utf-8")
    assert "alleen in het log" in log


def _slot_op(monkeypatch, naam):
    import os

    from tuinscan import bestanden

    echt = os.replace

    def slot(a, b):
        if str(b).endswith(naam):
            raise PermissionError(13, "in gebruik", str(b))
        return echt(a, b)

    monkeypatch.setattr(bestanden.os, "replace", slot)


def test_tuinen_met_open_gpkg_laat_vhe_parquet_staan(miniwijk, monkeypatch, capsys):
    mw = miniwijk
    stap_tuinen(mw.cfg, mw.mappen)
    rd = mw.mappen.ronde_dir
    pd.DataFrame({"vhe_nummer": ["oud"]}).to_parquet(rd / "vhe.parquet", index=False)
    _slot_op(monkeypatch, "tuinen.gpkg")
    assert main(["tuinen", "--config", str(mw.config_pad)]) == 1
    err = capsys.readouterr().err
    assert "Sluit bestand" in err and "tuinen.gpkg" in err and "QGIS" in err
    assert pd.read_parquet(rd / "vhe.parquet")["vhe_nummer"].tolist() == ["oud"]
    assert not [p.name for p in rd.iterdir() if ".tmp" in p.name]


def test_score_met_open_gpkg_schrijft_geen_halve_uitvoer(miniwijk, monkeypatch, capsys):
    mw = miniwijk
    stap_tuinen(mw.cfg, mw.mappen)
    _slot_op(monkeypatch, "tuinen.gpkg")
    assert main(["score", "--config", str(mw.config_pad)]) == 1
    rd = mw.mappen.ronde_dir
    assert not (rd / "scores.parquet").exists() and not (rd / "signalen.parquet").exists()
    assert "Sluit bestand" in capsys.readouterr().err


def test_console_output_fits_windows_codepage(miniwijk, monkeypatch, capsys):
    mw = miniwijk
    monkeypatch.setattr(
        "tuinscan.download.voer_download_uit",
        lambda *a: type("R", (), {"opgehaald": [], "overgeslagen": ["x"], "mislukt": {}})(),
    )
    assert main(["alles", "--config", str(mw.config_pad)]) == 0
    assert main(["kalibreer", "--config", str(mw.config_pad)]) == 0
    uit = capsys.readouterr()
    (uit.out + uit.err).encode("cp1252")  # geen tekens als '≥' die een Windows-console breken
