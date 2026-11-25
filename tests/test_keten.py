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
    assert not ronde.exists() and not any(cache.iterdir())
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
    try:
        (cache / "link").symlink_to(doel, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks niet toegestaan")
    stap_opruimen(mw.cfg, mw.mappen, ronde.name)
    assert not (cache / "link").is_symlink() and (doel / "belangrijk.txt").exists()


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
