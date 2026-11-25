import pytest
from openpyxl import load_workbook

from tuinscan.fouten import Foutenlijst
from tuinscan.tobias import lees_ruwe_export, lees_vhes, stel_mapping_voor
from tuinscan.voorbeeld import KOPPEN, maak_voorbeeld_export


def test_voorbeeld_roundtrip(miniwijk):
    mw = miniwijk
    pad = maak_voorbeeld_export(mw.cfg, mw.mappen, n=10, seed=3)
    assert pad.name == "voorbeeld_export.xlsx"
    assert pad != mw.mappen.invoer / mw.cfg.tobias.bestand  # echte export blijft heel
    ws = load_workbook(pad).active
    assert [c.value for c in ws[1]] == KOPPEN
    assert isinstance(ws["B2"].value, int)  # BAG-id als getal
    df = lees_ruwe_export(pad, None, None)
    voorstel = stel_mapping_voor(df)
    assert voorstel["bag_vbo_id"].kolom == "BAG VBO" and voorstel["bag_vbo_id"].zekerheid == "hoog"
    mapping = {v: m.kolom for v, m in voorstel.items() if m.kolom}
    assert mapping["vhe_nummer"] == "Eenheid" and mapping["complex"] == "Complexnr"
    # alle vier miniwijk-woningen zijn grondgebonden (rij: 100 m2 / 3 woningen)
    assert len(df) == 4 and set(df["Type"]) == {"eengezinswoning"}
    assert sorted(df["Eenheid"]) == ["VB00001", "VB00002", "VB00003", "VB00004"]


def test_voorbeeld_leest_door_lees_vhes(miniwijk, tmp_path):
    import dataclasses

    import yaml

    mw = miniwijk
    pad = maak_voorbeeld_export(mw.cfg, mw.mappen)
    voorstel = stel_mapping_voor(lees_ruwe_export(pad, None, None))
    (mw.mappen.invoer / "kolommen.yaml").write_text(
        yaml.safe_dump({v: m.kolom for v, m in voorstel.items() if m.kolom}), encoding="utf-8"
    )
    cfg = dataclasses.replace(
        mw.cfg, tobias=dataclasses.replace(mw.cfg.tobias, bestand=pad.name)
    )
    fouten = Foutenlijst()
    vhes = lees_vhes(cfg, mw.mappen, fouten)
    assert len(vhes) == 4 and not fouten.regels
    assert vhes["bag_vbo_id"].str.len().eq(16).all()


def test_voorbeeld_zonder_cache(miniwijk):
    import shutil

    from tuinscan.stappen import StapFout

    shutil.rmtree(miniwijk.mappen.cache)
    with pytest.raises(StapFout):
        maak_voorbeeld_export(miniwijk.cfg, miniwijk.mappen)
