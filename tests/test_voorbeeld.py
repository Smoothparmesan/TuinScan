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
    # alleen V is grondgebonden: het rij-pand van de miniwijk bevat drie woon-vbo's
    assert len(df) == 1 and set(df["Type"]) == {"eengezinswoning"}
    assert list(df["Eenheid"]) == ["VB00001"]


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
    assert len(vhes) == 1 and not fouten.regels
    assert vhes["bag_vbo_id"].str.len().eq(16).all()


def test_voorbeeld_zonder_cache(miniwijk):
    import shutil

    from tuinscan.stappen import StapFout

    shutil.rmtree(miniwijk.mappen.cache)
    with pytest.raises(StapFout):
        maak_voorbeeld_export(miniwijk.cfg, miniwijk.mappen)


def test_voorbeeld_pand_met_een_woning_per_huis(miniwijk):
    """Rijwoningen als apart pand met elk één vbo tellen wel mee."""
    import geopandas as gpd

    from tuinscan.download import schrijf_vector
    from tuinscan.pdok.cache import vector_pad
    from tuinscan.stappen import _lees_cache

    mw = miniwijk
    panden = _lees_cache(mw.mappen, "bag_pand")
    vbo = _lees_cache(mw.mappen, "bag_vbo")
    rij = panden[panden["identificatie"] == mw.pand_ids["rij"]].iloc[0]
    delen = []
    for k, h in enumerate(("R1", "R2", "R3")):
        x0 = rij.geometry.bounds[0] + 6 * k
        from shapely.geometry import box

        delen.append({"feature_id": f"x-{k}", "identificatie": f"9{k}",
                      "aantal_verblijfsobjecten": 1, "gebruiksdoel": "woonfunctie",
                      "geometry": box(x0, rij.geometry.bounds[1], x0 + 6, rij.geometry.bounds[3])})
        i = vbo.index[vbo["identificatie"] == mw.vbo_ids[h]][0]
        vbo.at[i, "pand.href"] = [f"https://x/items/x-{k}"]
    nieuw = gpd.GeoDataFrame(
        [*panden[panden["identificatie"] != mw.pand_ids["rij"]].to_dict("records"), *delen],
        crs=28992,
    )
    schrijf_vector(nieuw, vector_pad(mw.mappen.cache, "bag_pand"))
    schrijf_vector(vbo, vector_pad(mw.mappen.cache, "bag_vbo"))
    pad = maak_voorbeeld_export(mw.cfg, mw.mappen)
    assert len(lees_ruwe_export(pad, None, None)) == 4


def test_voorbeeld_slaat_rij_zonder_huisnummer_over(miniwijk):
    from tuinscan.download import schrijf_vector
    from tuinscan.pdok.cache import vector_pad
    from tuinscan.stappen import _lees_cache

    mw = miniwijk
    vbo = _lees_cache(mw.mappen, "bag_vbo")
    vbo.loc[vbo["identificatie"] == mw.vbo_ids["V"], "huisnummer"] = None
    schrijf_vector(vbo, vector_pad(mw.mappen.cache, "bag_vbo"))
    from tuinscan.stappen import StapFout

    with pytest.raises(StapFout):  # de enige kandidaat valt af, geen crash
        maak_voorbeeld_export(mw.cfg, mw.mappen)


def test_voorbeeld_cli(miniwijk, capsys):
    from tuinscan.cli import main

    assert main(["voorbeeld", "--config", str(miniwijk.config_pad), "--aantal", "5"]) == 0
    out = capsys.readouterr().out
    assert "voorbeeld_export.xlsx" in out and "tobias.bestand" in out
    assert (miniwijk.mappen.invoer / "voorbeeld_export.xlsx").exists()


def test_voorbeeld_open_in_excel_keeps_old_file(miniwijk, monkeypatch):
    import os

    from tuinscan import bestanden
    from tuinscan.bestanden import BestandOpen

    mw = miniwijk
    pad = maak_voorbeeld_export(mw.cfg, mw.mappen, n=10, seed=3)
    oud = pad.read_bytes()

    def slot(a, b):
        raise PermissionError(13, "in gebruik", str(b))

    monkeypatch.setattr(bestanden.os, "replace", slot)
    with pytest.raises(BestandOpen):
        maak_voorbeeld_export(mw.cfg, mw.mappen, n=10, seed=4)
    assert pad.read_bytes() == oud and sorted(os.listdir(pad.parent)).count(pad.name) == 1
    assert not [n for n in os.listdir(pad.parent) if ".tmp" in n]
