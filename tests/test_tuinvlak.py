import dataclasses
import json
import logging
from itertools import combinations

import geopandas as gpd
import pyogrio
import shapely
from shapely.geometry import MultiPolygon, Point

from miniwijk import rd, rd_box
from tuinscan.cli import main
from tuinscan.config import parameterset_hash
from tuinscan.fouten import Foutenlijst
from tuinscan.stappen import stap_tuinen
from tuinscan.tuinvlak import (
    GEEN_VOORZIJDE,
    deel_voor_achter,
    is_woon,
    pas_correcties_toe,
    splits_perceel,
    woon_zaden,
)


def _woon_vbo(mw):
    v = mw.cfg.pdok.velden
    return mw.vbo[mw.vbo[v["vbo_gebruiksdoel"]].map(lambda w: is_woon(w, v["woonfunctie"]))]


def _perceel(mw, p):
    v = mw.cfg.pdok.velden["perceel_id"]
    return mw.percelen[mw.percelen[v] == mw.perceel_ids[p]].geometry.iloc[0]


def _pand(mw, p):
    v = mw.cfg.pdok.velden["pand_id"]
    return mw.panden[mw.panden[v] == mw.pand_ids[p]].geometry.iloc[0]


def _cellen(mw, p, perceel=None, max_diepte=None):
    perceel = _perceel(mw, p) if perceel is None else perceel
    zaden = woon_zaden(perceel, _woon_vbo(mw), mw.panden, mw.cfg.pdok.velden["vbo_id"])
    return zaden, splits_perceel(
        perceel, zaden, mw.panden, mw.cfg.tuin.max_diepte_m if max_diepte is None else max_diepte
    )


def _cfg_delen(mw, delen):
    return dataclasses.replace(mw.cfg, tuin=dataclasses.replace(mw.cfg.tuin, delen=delen))


def _lees_tuinen(mw):
    return gpd.read_file(mw.mappen.ronde_dir / "tuinen.gpkg", layer="tuinvlak")


def test_seed_is_pand_centroid_for_single_house_else_vbo_point(miniwijk):
    zaden = woon_zaden(
        _perceel(miniwijk, "P2"), _woon_vbo(miniwijk), miniwijk.panden, "identificatie"
    )
    assert list(zaden["vbo_id"]) == [miniwijk.vbo_ids["V"]]
    assert zaden.geometry.iloc[0].equals(_pand(miniwijk, "vrijstaand").centroid)
    zaden = woon_zaden(
        _perceel(miniwijk, "P1"), _woon_vbo(miniwijk), miniwijk.panden, "identificatie"
    )
    assert sorted(zaden["vbo_id"]) == sorted(miniwijk.vbo_ids[h] for h in ("R1", "R2", "R3"))
    assert zaden.geometry.iloc[0].equals(Point(rd(3, 13)))


def test_detached_tuin_excludes_house_and_shed(miniwijk):
    zaden, cellen = _cellen(miniwijk, "P2")
    assert len(zaden) == 1
    tuin = cellen[miniwijk.vbo_ids["V"]]
    assert tuin.intersection(miniwijk.panden.union_all()).area == 0
    verwacht = _perceel(miniwijk, "P2").area - 8 * 10 - 3 * 3
    assert abs(tuin.area - verwacht) < 1e-6


def test_row_split_three_cells(miniwijk):
    _, cellen = _cellen(miniwijk, "P1")
    assert len(cellen) == 3
    for a, b in combinations(cellen.values(), 2):
        assert a.intersection(b).area < 0.01
    verwacht = _perceel(miniwijk, "P1").difference(_pand(miniwijk, "rij"))
    assert shapely.union_all(list(cellen.values())).symmetric_difference(verwacht).area < 0.01


def test_row_cut_perpendicular_to_row(miniwijk):
    _, cellen = _cellen(miniwijk, "P1")
    grens = cellen[miniwijk.vbo_ids["R1"]].intersection(cellen[miniwijk.vbo_ids["R2"]])
    assert grens.length > 0
    x0, _, x1, _ = grens.bounds
    assert abs(x1 - x0) < 0.01
    assert abs(x0 - rd(6, 0)[0]) < 0.01


def test_front_is_road_side(miniwijk):
    _, cellen = _cellen(miniwijk, "P1")
    pand = _pand(miniwijk, "rij")
    delen = dict(deel_voor_achter(
        cellen[miniwijk.vbo_ids["R1"]], pand, Point(rd(3, 13)), miniwijk.wegdelen, 30
    ))  # fmt: skip
    assert set(delen) == {"voor", "achter"}
    assert delen["voor"].centroid.y < pand.centroid.y
    assert delen["achter"].centroid.y > pand.centroid.y
    assert abs(delen["voor"].area - 6 * 6) < 1e-6


def test_detached_house_gets_front_part(miniwijk):
    _, cellen = _cellen(miniwijk, "P2")
    pand = _pand(miniwijk, "vrijstaand")
    delen = dict(deel_voor_achter(
        cellen[miniwijk.vbo_ids["V"]], pand, Point(rd(25, 13)), miniwijk.wegdelen, 30
    ))  # fmt: skip
    assert set(delen) == {"voor", "achter"}
    assert delen["voor"].bounds[3] <= pand.centroid.y + 1e-6
    assert delen["achter"].bounds[1] >= pand.centroid.y - 1e-6


def test_no_road_all_achter(miniwijk):
    _, cellen = _cellen(miniwijk, "P1")
    tuin = cellen[miniwijk.vbo_ids["R1"]]
    fouten = Foutenlijst()
    delen = deel_voor_achter(
        tuin, _pand(miniwijk, "rij"), Point(rd(3, 13)), miniwijk.wegdelen.iloc[0:0], 30,
        fouten=fouten, vhe_nummer="100001",
    )  # fmt: skip
    assert [d for d, _ in delen] == ["achter"]
    assert delen[0][1].equals(tuin)
    assert fouten.regels == [("100001", "tuinvlak", GEEN_VOORZIJDE)]
    assert "voorzijde niet bepaald" in GEEN_VOORZIJDE


def test_road_beyond_search_distance_all_achter(miniwijk):
    _, cellen = _cellen(miniwijk, "P1")
    fouten = Foutenlijst()
    delen = deel_voor_achter(
        cellen[miniwijk.vbo_ids["R1"]], _pand(miniwijk, "rij"), Point(rd(3, 13)),
        miniwijk.wegdelen, 5, fouten=fouten, vhe_nummer="100001",
    )  # fmt: skip
    assert [d for d, _ in delen] == ["achter"]
    assert fouten.aantal == 1


def test_max_depth(miniwijk):
    diep = rd_box((18, 2, 34, 80))  # zeer diep perceel P2
    _, cellen = _cellen(miniwijk, "P2", perceel=diep, max_diepte=25)
    tuin = cellen[miniwijk.vbo_ids["V"]]
    pand = _pand(miniwijk, "vrijstaand")
    assert tuin.within(pand.buffer(25 + 1e-6))
    assert tuin.bounds[3] < rd(0, 18 + 25 + 0.01)[1]
    assert tuin.area < diep.area - 80 - 9 - 100


def test_delen_voor_only(miniwijk):
    stap_tuinen(_cfg_delen(miniwijk, ["voor"]), miniwijk.mappen)
    t = _lees_tuinen(miniwijk)
    assert set(t["deel"]) == {"voor"}
    assert len(t) == 4
    stap_tuinen(_cfg_delen(miniwijk, ["voor", "achter"]), miniwijk.mappen)
    t = _lees_tuinen(miniwijk)
    assert set(t["deel"]) == {"voor", "achter"}
    assert len(t) == 8
    rij = t[t["vhe_nummer"] == miniwijk.vhe_nummers["R2"]]
    assert set(rij["methode"]) == {"gesplitst"}
    assert set(t[t["vhe_nummer"] == miniwijk.vhe_nummers["V"]]["methode"]) == {"perceel"}


def test_correction_replaces_geometry(miniwijk, tmp_path):
    correctie = rd_box((0, 2, 6, 7))
    gpd.GeoDataFrame(
        {"vhe_nummer": [miniwijk.vhe_nummers["R1"]], "deel": ["voor"]},
        geometry=[MultiPolygon([correctie])], crs=28992,
    ).to_file(miniwijk.mappen.invoer / "tuincorrecties.gpkg", layer="tuinvlak")  # fmt: skip
    stap_tuinen(miniwijk.cfg, miniwijk.mappen)
    t = _lees_tuinen(miniwijk)
    r1 = t[(t["vhe_nummer"] == miniwijk.vhe_nummers["R1"]) & (t["deel"] == "voor")]
    assert len(r1) == 1
    assert r1.geometry.iloc[0].equals(correctie)
    assert r1["methode"].iloc[0] == "handmatig"
    assert abs(r1["oppervlak_m2"].iloc[0] - 30) < 1e-6
    r2 = t[t["vhe_nummer"] == miniwijk.vhe_nummers["R2"]]
    assert set(r2["methode"]) == {"gesplitst"}


def test_correction_for_unknown_vhe_ignored(miniwijk, tmp_path, caplog):
    tuinen = gpd.GeoDataFrame(
        {"vhe_nummer": ["100001"], "deel": ["voor"], "methode": ["gesplitst"], "adres": ["a"],
         "wijk": ["w"], "complex": ["c"]},
        geometry=[rd_box((0, 2, 6, 8))], crs=28992,
    )  # fmt: skip
    pad = tmp_path / "corr.gpkg"
    gpd.GeoDataFrame(
        {"vhe_nummer": [999999, 100001], "deel": ["voor", "achter"]},
        geometry=[rd_box((0, 0, 1, 1)), rd_box((0, 18, 6, 32))], crs=28992,
    ).to_file(pad, layer="tuinvlak")  # fmt: skip
    with caplog.at_level(logging.WARNING):
        uit = pas_correcties_toe(tuinen, pad)
    assert "999999" in caplog.text
    assert "999999" not in set(uit["vhe_nummer"])
    achter = uit[uit["deel"] == "achter"]
    assert len(achter) == 1 and achter["methode"].iloc[0] == "handmatig"
    assert achter["adres"].iloc[0] == "a"
    assert pas_correcties_toe(tuinen, tmp_path / "bestaat_niet.gpkg").equals(tuinen)


def test_too_small_goes_to_foutenlijst(miniwijk):
    cfg = dataclasses.replace(
        miniwijk.cfg, tuin=dataclasses.replace(miniwijk.cfg.tuin, min_oppervlak_m2=40)
    )
    r = stap_tuinen(cfg, miniwijk.mappen)
    assert r.n_tuin == 1  # alleen V heeft een voortuin >= 40 m2
    fouten = (miniwijk.mappen.ronde_dir / "foutenlijst.csv").read_text(encoding="utf-8")
    assert fouten.count("tuinvlak te klein") == 3


def test_stap_tuinen_percentage(miniwijk, capsys):
    r = stap_tuinen(miniwijk.cfg, miniwijk.mappen)
    assert (r.n_vhe, r.n_tuin, r.percentage) == (4, 4, 100.0)
    out = capsys.readouterr().out
    assert "Koppeling + tuinvlak gelukt voor 4 van 4 VHE's (100 %)" in out
    rd_ = miniwijk.mappen.ronde_dir
    df = pyogrio.read_dataframe(rd_ / "tuinen.gpkg", layer="tuinvlak")
    assert list(df.columns) == ["vhe_nummer", "deel", "methode", "oppervlak_m2", "adres", "wijk",
                                "complex", "geometry"]  # fmt: skip
    assert set(df["vhe_nummer"]) == set(miniwijk.vhe_nummers.values())
    assert df.crs.to_epsg() == 28992
    ronde = json.loads((rd_ / "ronde.json").read_text(encoding="utf-8"))
    assert ronde["parameterset"] == parameterset_hash(miniwijk.cfg)
    assert ronde["gebied"]["naam"] == "miniwijk"
    assert ronde["gebied"]["bbox_rd"] == list(miniwijk.cfg.gebied.bbox_rd)
    assert ronde["filters"] == miniwijk.cfg.tobias.filters
    assert ronde["kolommen"]["vhe_nummer"] == "Eenheid"
    assert len(ronde["datum"]) == 10
    assert (rd_ / "foutenlijst.csv").exists()


def test_stap_tuinen_warns_below_90(miniwijk, capsys):
    vbo = miniwijk.vbo.copy()
    vbo = vbo[vbo["identificatie"] != miniwijk.vbo_ids["V"]]
    from tuinscan.download import schrijf_vector
    from tuinscan.pdok.cache import vector_pad

    schrijf_vector(vbo, vector_pad(miniwijk.mappen.cache, "bag_vbo"))
    r = stap_tuinen(miniwijk.cfg, miniwijk.mappen)
    assert (r.n_vhe, r.n_tuin) == (4, 3)
    out = capsys.readouterr().out
    assert "3 van 4 VHE's (75 %)" in out
    assert "minder dan 90 %" in out


def test_cli_tuinen(miniwijk, capsys):
    assert main(["tuinen", "--config", str(miniwijk.config_pad)]) == 0
    assert "gelukt voor 4 van 4" in capsys.readouterr().out
    assert (miniwijk.mappen.ronde_dir / "tuinen.gpkg").exists()


def test_cli_tuinen_without_cache(miniwijk, capsys):
    (miniwijk.mappen.cache / "vector" / "brk_perceel.gpkg").unlink()
    assert main(["tuinen", "--config", str(miniwijk.config_pad)]) == 1
    assert "download" in capsys.readouterr().err
