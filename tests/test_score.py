import dataclasses

import geopandas as gpd
import pandas as pd
import pytest

from tuinscan import stappen
from tuinscan.config import parameterset_hash
from tuinscan.score import aggregeer_delen, bereken_score, schaal
from tuinscan.stappen import stap_score, stap_tuinen


def _boven(cfg):
    return {k: r.boven for k, r in cfg.score.items()}


def test_schaal_clips():
    assert schaal(-1, 0, 1) == 0 and schaal(2, 0, 1) == 1 and schaal(0.5, 0, 1) == 0.5


def test_score_max_is_100(miniwijk):
    u = bereken_score(_boven(miniwijk.cfg), miniwijk.cfg)
    assert u.score == 100.0 and u.ontbrekend == []


def test_missing_signal_counts_zero(miniwijk):
    sig = {**_boven(miniwijk.cfg), "S1": None}
    u = bereken_score(sig, miniwijk.cfg)
    assert u.score == 75.0 and u.ontbrekend == ["S1"]


def test_two_heaviest_reasons(miniwijk):
    cfg = miniwijk.cfg
    sig = {"S1": cfg.score["S1"].onder, "S2": cfg.score["S2"].onder,
           "S3": cfg.score["S3"].boven, "S4a": cfg.score["S4a"].boven / 2,
           "S5": 0.5 + 0.5 * 0.2}  # fmt: skip
    u = bereken_score(sig, cfg)
    assert u.bijdragen["S3"] == pytest.approx(20) and u.bijdragen["S4a"] == pytest.approx(12.5)
    assert u.bijdragen["S5"] == pytest.approx(2)
    assert u.reden_codes == ["S3", "S4a"] and len(u.redenen) == 2
    assert u.redenen[0].startswith("Overhang: 5.0 m²")


def test_no_reasons_when_all_zero(miniwijk):
    sig = {k: r.onder for k, r in miniwijk.cfg.score.items()}
    u = bereken_score(sig, miniwijk.cfg)
    assert u.score == 0.0 and u.redenen == [] and u.reden_codes == []


def test_aggregate_parts():
    delen = [
        (10.0, {"S1": 0.2, "S3": 1.0, "S5": None}),
        (30.0, {"S1": None, "S3": 2.0, "S5": None}),
    ]
    a = aggregeer_delen(delen)
    assert a["S1"] == pytest.approx(0.2) and a["S3"] == 3.0 and a["S5"] is None
    delen = [(10.0, {"S2": 0.0}), (30.0, {"S2": 0.4})]
    assert aggregeer_delen(delen)["S2"] == pytest.approx(0.3)


def _cfg_beide(mw):
    return dataclasses.replace(
        mw.cfg, tuin=dataclasses.replace(mw.cfg.tuin, delen=["voor", "achter"])
    )


def test_fixed_scale_not_per_wijk(miniwijk):
    mw = miniwijk
    cfg = _cfg_beide(mw)
    stap_tuinen(cfg, mw.mappen)
    sc = stap_score(cfg, mw.mappen)
    assert sc["score"].between(0, 100).all()
    for r in sc.itertuples():  # score hangt alleen van de eigen signalen af, niet van de wijk
        eigen = {k: getattr(r, k) for k in mw.cfg.score}
        eigen = {k: (None if pd.isna(v) else v) for k, v in eigen.items()}
        assert bereken_score(eigen, cfg).score == r.score
    assert (sc.groupby("wijk")["rang_in_wijk"].min() == 1).all()
    assert bereken_score({"S3": 100.0}, cfg).score == 20.0


def test_provenance_columns(miniwijk):
    mw = miniwijk
    stap_tuinen(mw.cfg, mw.mappen)
    sc = stap_score(mw.cfg, mw.mappen)
    rd = mw.mappen.ronde_dir
    gelezen = pd.read_parquet(rd / "scores.parquet")
    lagen = mw.cfg.luchtfoto.lagen
    assert (gelezen["zomer_jaar"] == lagen["zomer_nu"]["jaar"]).all()
    assert (gelezen["zomer_vorig_jaar"] == lagen["zomer_vorig"]["jaar"]).all()
    assert (gelezen["winter_jaar"] == lagen["winter_nu"]["jaar"]).all()
    assert (gelezen["ahn_versie"] == mw.cfg.ahn.versie).all()
    assert (gelezen["parameterset"] == parameterset_hash(mw.cfg)).all()
    assert (rd / "signalen.parquet").exists()
    laag = gpd.read_file(rd / "tuinen.gpkg", layer="scores")
    assert len(laag) == len(sc) and "score" in laag.columns
    assert laag["reden_codes"].map(lambda v: isinstance(v, str) or v is None).all()


def test_one_bad_vhe_does_not_stop(miniwijk, monkeypatch):
    mw = miniwijk
    stap_tuinen(mw.cfg, mw.mappen)
    echt = stappen.bereken_signalen
    calls = {"n": 0}

    def kapot(tr, cfg, negeer):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("stuk")
        return echt(tr, cfg, negeer)

    monkeypatch.setattr(stappen, "bereken_signalen", kapot)
    sc = stap_score(mw.cfg, mw.mappen)
    fouten = pd.read_csv(mw.mappen.ronde_dir / "foutenlijst.csv", dtype=str)
    assert (fouten["stap"] == "score").sum() == 1
    assert len(sc) >= 1


def test_negeer_berging_requires_column_and_no_bijgebouw(miniwijk, monkeypatch):
    mw = miniwijk
    stap_tuinen(mw.cfg, mw.mappen)
    gezien = {}
    echt = stappen.bereken_signalen

    def spion(tr, cfg, negeer):
        gezien[len(gezien)] = negeer
        return echt(tr, cfg, negeer)

    monkeypatch.setattr(stappen, "bereken_signalen", spion)
    stap_score(mw.cfg, mw.mappen)
    assert not any(gezien.values())  # geen kolom standaardberging
    vhe = pd.read_parquet(mw.mappen.ronde_dir / "vhe.parquet")
    vhe["standaardberging"] = "Ja"
    vhe.to_parquet(mw.mappen.ronde_dir / "vhe.parquet", index=False)
    gezien.clear()
    stap_score(mw.cfg, mw.mappen)
    # schuur staat op perceel P2 (V) -> bijgebouw; rijwoningen op P1 hebben geen bijgebouw
    assert any(gezien.values()) and not all(gezien.values())
