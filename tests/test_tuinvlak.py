import dataclasses
import json
import logging
import warnings
from itertools import combinations

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
from openpyxl import load_workbook
from shapely.geometry import MultiPolygon, Point, Polygon

from miniwijk import HUIZEN, PAND_FEATURE_IDS, PAND_ITEMS, rd, rd_box
from tuinscan.cli import main
from tuinscan.config import parameterset_hash
from tuinscan.download import schrijf_vector
from tuinscan.fouten import Foutenlijst
from tuinscan.pdok.cache import vector_pad
from tuinscan.stappen import stap_tuinen
from tuinscan.tuinvlak import (
    GEDEELD,
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
    vhes = pd.DataFrame({"vhe_nummer": ["100001"], "adres": ["a"], "wijk": ["w"],
                         "complex": ["c"]})  # fmt: skip
    with caplog.at_level(logging.WARNING):
        uit = pas_correcties_toe(tuinen, pad, vhes)
    assert "999999" in caplog.text
    assert "999999" not in set(uit["vhe_nummer"])
    achter = uit[uit["deel"] == "achter"]
    assert len(achter) == 1 and achter["methode"].iloc[0] == "handmatig"
    assert achter["adres"].iloc[0] == "a"
    assert pas_correcties_toe(tuinen, tmp_path / "bestaat_niet.gpkg", vhes).equals(tuinen)


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


# --- review-fixes: scheidingsmuren, correcties, geldigheid, gestapelde woningen ----------


def _scene(panden, vbos, perceel=(0, 2, 18, 32)):
    """Lokale scène: panden {naam: rect of Polygon}, vbos {id: (punt, pand)}."""

    def geom(g):
        return rd_box(g) if isinstance(g, tuple) else g

    pgdf = gpd.GeoDataFrame({"identificatie": list(panden)},
                            geometry=[geom(g) for g in panden.values()], crs=28992)  # fmt: skip
    vgdf = gpd.GeoDataFrame(
        {"identificatie": list(vbos), "gebruiksdoel": "woonfunctie"},
        geometry=[Point(rd(*pt)) for pt, _ in vbos.values()], crs=28992,
    )  # fmt: skip
    return rd_box(perceel), vgdf, pgdf


def _split(perceel, vgdf, pgdf, **kw):
    zaden = woon_zaden(perceel, vgdf, pgdf, "identificatie", **kw)
    return zaden, splits_perceel(perceel, zaden, pgdf, 25)


def _grens_x(a, b, ymin=None):
    """x-bereik van de gedeelde grens tussen twee tuinvlakken (optioneel boven ymin)."""
    g = a.intersection(b)
    if ymin is not None:
        g = g.intersection(rd_box((-100, ymin, 100, 100)))
    assert g.length > 1
    return g.bounds[0] - rd(0, 0)[0], g.bounds[2] - rd(0, 0)[0]


VBO = {f"03630100000001{i:02d}": i for i in range(1, 10)}
IDS = list(VBO)


def test_party_wall_split_unequal_widths():
    perceel, vgdf, pgdf = _scene(
        {"A": (0, 8, 5, 18), "B": (5, 8, 12, 18), "C": (12, 8, 18, 18)},
        {IDS[0]: ((2, 13), "A"), IDS[1]: ((8, 13), "B"), IDS[2]: ((15, 13), "C")},
    )
    _, cel = _split(perceel, vgdf, pgdf)
    a, b, c = (cel[i] for i in IDS[:3])
    for lo, hi in (_grens_x(a, b), _grens_x(a, b, ymin=18)):
        assert abs(lo - 5) < 0.01 and abs(hi - 5) < 0.01
    lo, hi = _grens_x(b, c)
    assert abs(lo - 12) < 0.01 and abs(hi - 12) < 0.01
    assert abs(a.area - 5 * 20) < 0.01 and abs(b.area - 7 * 20) < 0.01
    verwacht = perceel.difference(pgdf.union_all())
    assert shapely.union_all([a, b, c]).symmetric_difference(verwacht).area < 0.01


def test_party_wall_split_with_rear_extension():
    perceel, vgdf, pgdf = _scene(
        {"A": (0, 8, 5, 18), "B": (5, 8, 12, 22), "C": (12, 8, 18, 18)},
        {IDS[0]: ((2, 13), "A"), IDS[1]: ((8, 13), "B"), IDS[2]: ((15, 13), "C")},
    )
    _, cel = _split(perceel, vgdf, pgdf)
    a, b, c = (cel[i] for i in IDS[:3])
    for paar in ((a, b), (b, c)):
        lo, hi = _grens_x(*paar, ymin=22)
        assert abs(hi - lo) < 0.01
    assert abs(_grens_x(a, b, ymin=22)[0] - 5) < 0.01
    assert abs(_grens_x(b, c, ymin=22)[0] - 12) < 0.01
    assert abs(b.area - (7 * 6 + 7 * 10)) < 0.01


def test_party_walls_combined_with_multi_vbo_pand():
    perceel, vgdf, pgdf = _scene(
        {"A": (0, 8, 5, 18), "B": (5, 8, 12, 18), "C": (12, 8, 18, 18), "flat": (24, 8, 32, 18)},
        {IDS[0]: ((2, 13), "A"), IDS[1]: ((8, 13), "B"), IDS[2]: ((15, 13), "C"),
         IDS[3]: ((26, 13), "flat"), IDS[4]: ((30, 13), "flat")},
        perceel=(0, 2, 34, 32),
    )  # fmt: skip
    _, cel = _split(perceel, vgdf, pgdf)
    assert len(cel) == 5
    lo, hi = _grens_x(cel[IDS[0]], cel[IDS[1]])
    assert abs(lo - 5) < 0.01 and abs(hi - 5) < 0.01
    for x, y in combinations(cel.values(), 2):
        assert x.intersection(y).area < 0.01
    verwacht = perceel.difference(pgdf.union_all())
    assert shapely.union_all(list(cel.values())).symmetric_difference(verwacht).area < 0.01


def test_stacked_dwellings_merge_into_one_seed():
    perceel, vgdf, pgdf = _scene(
        {"huis": (21, 8, 29, 18)},
        {IDS[0]: ((25, 13), "huis"), IDS[1]: ((25.4, 13), "huis")},
        perceel=(18, 2, 34, 32),
    )
    zaden, cel = _split(perceel, vgdf, pgdf)
    assert list(zaden["gedeeld"]) == [2, 2]
    assert zaden.geometry.iloc[0].equals(zaden.geometry.iloc[1])
    assert cel[IDS[0]].equals(cel[IDS[1]])
    assert abs(cel[IDS[0]].area - (16 * 30 - 80)) < 1e-6


def test_seed_uses_koppel_pand():
    perceel, vgdf, pgdf = _scene({"huis": (21, 8, 29, 18)}, {IDS[0]: ((25, 20), "huis")},
                                 perceel=(18, 2, 34, 32))  # fmt: skip
    zaden = woon_zaden(perceel, vgdf, pgdf, "identificatie")
    assert zaden["pand"].iloc[0] is None
    zaden = woon_zaden(perceel, vgdf, pgdf, "identificatie", eigen_pand={IDS[0]: 0})
    assert zaden["pand"].iloc[0] == 0
    assert zaden.geometry.iloc[0].equals(pgdf.geometry.iloc[0].centroid)


def _schrijf_cache(mw, sleutel, gdf):
    schrijf_vector(gdf, vector_pad(mw.mappen.cache, sleutel))


def test_stacked_vhes_share_tuin_with_fout(miniwijk):
    v2 = "0363010000000022"
    extra = miniwijk.vbo[miniwijk.vbo["identificatie"] == miniwijk.vbo_ids["V"]].copy()
    extra["identificatie"] = v2
    extra["feature_id"] = "b0000000-0000-4000-8000-000000000022"
    extra["pand.href"] = [[PAND_ITEMS + PAND_FEATURE_IDS["vrijstaand"]]]
    extra = extra.set_geometry([Point(rd(25.4, 13))], crs=28992)
    _schrijf_cache(miniwijk, "bag_vbo", pd.concat([miniwijk.vbo, extra], ignore_index=True))
    xlsx = miniwijk.mappen.invoer / miniwijk.cfg.tobias.bestand
    wb = load_workbook(xlsx)
    wb.active.append([100005, v2, "Testwijk", "C2", "vrijstaand", "Tuinstraat", "7A", "1000AA",
                      "Amsterdam"])  # fmt: skip
    wb.save(xlsx)
    r = stap_tuinen(miniwijk.cfg, miniwijk.mappen)
    assert (r.n_vhe, r.n_tuin) == (5, 5)
    t = _lees_tuinen(miniwijk)
    v, v2t = (t[t["vhe_nummer"] == n].geometry.iloc[0] for n in ("100004", "100005"))
    assert v.equals(v2t)
    fouten = (miniwijk.mappen.ronde_dir / "foutenlijst.csv").read_text(encoding="utf-8")
    assert fouten.count(GEDEELD) == 2
    assert "gedeelde tuin (meerdere woningen in één pand)" in GEDEELD


def test_bowtie_pand_does_not_break_complex_perceel(miniwijk):
    strik = Polygon([rd(2, 25), rd(6, 29), rd(6, 25), rd(2, 29)])
    assert not strik.is_valid
    panden = miniwijk.panden.copy()
    extra = gpd.GeoDataFrame(
        {"feature_id": ["a-strik"], "identificatie": ["0363100000000999"],
         "aantal_verblijfsobjecten": [0], "gebruiksdoel": ["overige gebruiksfunctie"]},
        geometry=[strik], crs=28992,
    )  # fmt: skip
    _schrijf_cache(miniwijk, "bag_pand", pd.concat([panden, extra], ignore_index=True))
    r = stap_tuinen(_cfg_delen(miniwijk, ["voor", "achter"]), miniwijk.mappen)
    assert r.n_tuin == 4
    t = _lees_tuinen(miniwijk)
    r1 = t[(t["vhe_nummer"] == miniwijk.vhe_nummers["R1"]) & (t["deel"] == "achter")]
    assert abs(r1.geometry.iloc[0].area - (6 * 14 - 8)) < 1e-6  # strik (2 driehoeken) eruit


def test_correction_adds_tuin_for_failed_vhe(miniwijk):
    vbo = miniwijk.vbo[miniwijk.vbo["identificatie"] != miniwijk.vbo_ids["V"]]
    _schrijf_cache(miniwijk, "bag_vbo", vbo)
    correctie = rd_box((18, 2, 34, 8))
    gpd.GeoDataFrame({"vhe_nummer": ["100004"], "deel": ["voor"]}, geometry=[correctie],
                     crs=28992).to_file(miniwijk.mappen.invoer / "tuincorrecties.gpkg",
                                        layer="tuinvlak")  # fmt: skip
    r = stap_tuinen(miniwijk.cfg, miniwijk.mappen)
    assert (r.n_vhe, r.n_tuin) == (4, 4)
    t = _lees_tuinen(miniwijk)
    v = t[t["vhe_nummer"] == "100004"]
    assert len(v) == 1 and v["methode"].iloc[0] == "handmatig"
    assert v["adres"].iloc[0] == "Tuinstraat 7"
    assert v["complex"].iloc[0] == HUIZEN["V"][4]
    assert v.geometry.iloc[0].equals(correctie)


def test_correction_crs_and_validity(tmp_path, caplog):
    tuinen = gpd.GeoDataFrame(
        {"vhe_nummer": ["1", "2"], "deel": ["voor", "voor"], "methode": "gesplitst",
         "adres": ["a", "b"], "wijk": "w", "complex": "c"},
        geometry=[rd_box((0, 2, 6, 8)), rd_box((6, 2, 12, 8))], crs=28992,
    )  # fmt: skip
    vhes = pd.DataFrame({"vhe_nummer": ["1", "2"], "adres": ["a", "b"], "wijk": "w",
                         "complex": "c"})  # fmt: skip
    strik = Polygon([rd(0, 2), rd(6, 8), rd(6, 2), rd(0, 8)])
    zonder_crs = tmp_path / "zonder.gpkg"
    with warnings.catch_warnings():  # bewust zonder CRS geschreven
        warnings.simplefilter("ignore", UserWarning)
        gpd.GeoDataFrame({"vhe_nummer": ["1"], "deel": ["voor"]}, geometry=[strik]).to_file(
            zonder_crs, layer="tuinvlak"
        )
    with caplog.at_level(logging.WARNING):
        uit = pas_correcties_toe(tuinen, zonder_crs, vhes)
    assert "28992" in caplog.text
    g = uit[uit["vhe_nummer"] == "1"].geometry.iloc[0]
    assert g.is_valid and abs(g.area - 18) < 1e-6  # twee driehoeken van 9 m2

    wgs = tmp_path / "wgs.gpkg"
    doel = rd_box((6, 2, 12, 7))
    gpd.GeoDataFrame({"vhe_nummer": ["2"], "deel": ["voor"]}, geometry=[doel],
                     crs=28992).to_crs(4326).to_file(wgs, layer="tuinvlak")  # fmt: skip
    uit = pas_correcties_toe(tuinen, wgs, vhes)
    g = uit[uit["vhe_nummer"] == "2"].geometry.iloc[0]
    assert g.symmetric_difference(doel).area < 0.01
    assert uit.crs.to_epsg() == 28992
