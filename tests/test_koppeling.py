import numpy as np
import pandas as pd
import pytest

from miniwijk import PAND_ITEMS
from tuinscan import koppeling
from tuinscan.download import VECTOR_SLEUTELS
from tuinscan.fouten import Foutenlijst
from tuinscan.koppeling import DUBBEL, NIET_GEVONDEN, koppel, koppelpercentage
from tuinscan.pdok.cache import Manifest
from tuinscan.tobias import BAG_HINT


def run(mw, vhes=None, vbo=None, panden=None, percelen=None):
    f = Foutenlijst()
    r = koppel(
        mw.vhes if vhes is None else vhes,
        mw.vbo if vbo is None else vbo,
        mw.panden if panden is None else panden,
        mw.percelen if percelen is None else percelen,
        mw.cfg.pdok.velden,
        f,
    )
    return r, f


def extra(mw, vhe, bag):
    rij = mw.vhes.iloc[[0]].copy()
    rij["vhe_nummer"], rij["bag_vbo_id"] = vhe, bag
    return pd.concat([mw.vhes, rij], ignore_index=True)


def per_vhe(r):
    return r.set_index("vhe_nummer")


def test_miniwijk_fixture_consistent(miniwijk):
    mw = miniwijk
    assert len(mw.vhes) == 4 and set(mw.vhes["bag_vbo_id"]) == set(mw.vbo_ids.values())
    assert all(isinstance(h, list) and len(h) == 1 for h in mw.vbo["pand.href"])
    m = Manifest(mw.mappen.cache / "manifest.json")
    for sleutel in VECTOR_SLEUTELS:
        assert m.heeft(sleutel, mw.bbox)
    assert mw.cfg.gebied.bbox_rd[0] < mw.percelen.total_bounds[0]
    assert mw.config_pad.exists() and (mw.mappen.invoer / "kolommen.yaml").exists()


def test_all_miniwijk_vhes_link(miniwijk):
    mw = miniwijk
    r, f = run(mw)
    assert f.aantal == 0 and len(r) == 4 and r.crs.to_epsg() == 28992
    for kol in ("vhe_nummer", "bag_vbo_id", "pand_id", "perceel_id", "geometry", "woningtype"):
        assert kol in r.columns
    v = per_vhe(r)
    rij = [mw.vhe_nummers[h] for h in ("R1", "R2", "R3")]
    assert set(v.loc[rij, "perceel_id"]) == {mw.perceel_ids["P1"]}
    assert set(v.loc[rij, "pand_id"]) == {mw.pand_ids["rij"]}
    vrij = v.loc[mw.vhe_nummers["V"]]
    assert vrij["perceel_id"] == mw.perceel_ids["P2"]
    assert vrij["pand_id"] == mw.pand_ids["vrijstaand"]
    assert vrij["bag_vbo_id"] == mw.vbo_ids["V"]
    assert vrij.geometry.geom_type == "Point"
    assert koppelpercentage(len(mw.vhes), len(r)) == 100.0


def test_unknown_vbo_to_foutenlijst(miniwijk):
    mw = miniwijk
    r, f = run(mw, vhes=extra(mw, "999", "0363010000099990"))
    assert len(r) == 4 and f.aantal == 1
    vhe, stap, reden = f.regels[0]
    assert (vhe, stap) == ("999", "koppeling")
    assert "BAG-verblijfsobject niet gevonden in werkgebied" in reden and BAG_HINT in reden
    _, f2 = run(mw, vhes=extra(mw, "998", "0363010000099991"))
    assert BAG_HINT not in f2.regels[0][2]


def test_two_vhes_one_vbo(miniwijk):
    mw = miniwijk
    r, f = run(mw, vhes=extra(mw, "999", mw.vbo_ids["R2"]))
    assert len(r) == 4 and "999" not in set(r["vhe_nummer"])
    assert per_vhe(r).loc[mw.vhe_nummers["R2"], "bag_vbo_id"] == mw.vbo_ids["R2"]
    assert f.aantal == 1
    vhe, stap, reden = f.regels[0]
    assert (vhe, stap) == ("999", "koppeling")
    assert "meerdere VHE's op hetzelfde verblijfsobject" in reden


def test_pand_via_href(miniwijk):
    mw = miniwijk
    vbo = mw.vbo.copy()
    i = vbo.index[vbo["identificatie"] == mw.vbo_ids["V"]][0]
    vbo.at[i, "pand.href"] = [PAND_ITEMS + mw.pand_feature_ids["schuur"]]  # punt ligt er niet in
    r, f = run(mw, vbo=vbo)
    assert f.aantal == 0
    assert per_vhe(r).loc[mw.vhe_nummers["V"], "pand_id"] == mw.pand_ids["schuur"]


def test_pand_several_hrefs_takes_containing(miniwijk):
    mw = miniwijk
    vbo = mw.vbo.copy()
    i = vbo.index[vbo["identificatie"] == mw.vbo_ids["V"]][0]
    vbo.at[i, "pand.href"] = [PAND_ITEMS + mw.pand_feature_ids[p] for p in ("schuur", "vrijstaand")]
    r, _ = run(mw, vbo=vbo)
    assert per_vhe(r).loc[mw.vhe_nummers["V"], "pand_id"] == mw.pand_ids["vrijstaand"]


def test_pand_fallback_spatial(miniwijk):
    mw = miniwijk
    vbo = mw.vbo.copy()
    vbo["pand.href"] = [[] for _ in range(len(vbo))]
    r, f = run(mw, vbo=vbo)
    assert f.aantal == 0 and len(r) == 4
    v = per_vhe(r)
    assert v.loc[mw.vhe_nummers["R1"], "pand_id"] == mw.pand_ids["rij"]
    assert v.loc[mw.vhe_nummers["V"], "pand_id"] == mw.pand_ids["vrijstaand"]


def test_geen_pand_and_geen_perceel(miniwijk):
    mw = miniwijk
    zonder_rij = mw.panden[mw.panden["identificatie"] != mw.pand_ids["rij"]]
    vbo = mw.vbo.copy()
    vbo["pand.href"] = [[] for _ in range(len(vbo))]
    r, f = run(mw, vbo=vbo, panden=zonder_rij)
    assert len(r) == 1 and f.aantal == 3
    assert all(s == "koppeling" and "geen pand" in reden for _, s, reden in f.regels)
    zonder_p2 = mw.percelen[mw.percelen["identificatie_lokaal_id"] != mw.perceel_ids["P2"]]
    r, f = run(mw, percelen=zonder_p2)
    assert len(r) == 3 and f.regels == [(mw.vhe_nummers["V"], "koppeling", f.regels[0][2])]
    assert "geen perceel" in f.regels[0][2]


def test_perceel_fallback_largest_overlap(miniwijk):
    mw = miniwijk
    # vbo-punt buiten elk perceel (in de straat), pand ligt op P2
    vbo = mw.vbo.copy()
    i = vbo.index[vbo["identificatie"] == mw.vbo_ids["V"]][0]
    x = vbo.geometry[i].x
    vbo.loc[i, "geometry"] = type(vbo.geometry[i])(x, mw.percelen.total_bounds[1] - 1)
    r, f = run(mw, vbo=vbo)
    assert f.aantal == 0
    assert per_vhe(r).loc[mw.vhe_nummers["V"], "perceel_id"] == mw.perceel_ids["P2"]


def test_bad_row_does_not_stop(miniwijk):
    mw = miniwijk
    vbo = mw.vbo.copy()
    i = vbo.index[vbo["identificatie"] == mw.vbo_ids["V"]][0]
    vbo.at[i, "pand.href"] = 12345  # onverwacht type
    r, f = run(mw, vbo=vbo)
    assert len(r) == 3 and f.aantal == 1 and f.regels[0][1] == "koppeling"
    vbo = mw.vbo.copy()
    vbo.loc[i, "geometry"] = None
    r, f = run(mw, vbo=vbo)
    assert len(r) == 3 and f.aantal == 1 and "geen geometrie" in f.regels[0][2]


def test_empty_vhes(miniwijk):
    r, f = run(miniwijk, vhes=miniwijk.vhes.iloc[0:0])
    assert len(r) == 0 and "perceel_id" in r.columns and f.aantal == 0


@pytest.mark.parametrize("n,k,verwacht", [(4, 3, 75.0), (0, 0, 0.0), (3, 3, 100.0)])
def test_koppelpercentage(n, k, verwacht):
    assert koppelpercentage(n, k) == verwacht


def test_duplicate_of_failed_first_vhe_is_not_dubbel(miniwijk, monkeypatch):
    mw = miniwijk
    bag = "0363010000099991"
    vhes = pd.concat([extra(mw, "998", bag), extra(mw, "999", bag).iloc[[-1]]], ignore_index=True)
    r, f = run(mw, vhes=vhes)
    assert [v for v, _, _ in f.regels] == ["998", "999"]
    assert all(NIET_GEVONDEN in reden and DUBBEL not in reden for _, _, reden in f.regels)

    echte_pand = koppeling._Koppelaar.pand
    aanroepen = []

    def eerste_faalt(self, vbo_rij):
        aanroepen.append(1)
        return None if len(aanroepen) == 1 else echte_pand(self, vbo_rij)

    monkeypatch.setattr(koppeling._Koppelaar, "pand", eerste_faalt)
    r, f = run(mw, vhes=extra(mw, "999", mw.vbo_ids["R1"]))
    assert len(r) == 4 and "999" in set(r["vhe_nummer"])
    assert f.regels == [(mw.vhe_nummers["R1"], "koppeling", f.regels[0][2])]
    assert "geen pand" in f.regels[0][2]


@pytest.mark.parametrize("maak", [np.array, pd.Series])
def testhref_ids_accepts_array_like(maak):
    h = maak([PAND_ITEMS + "abc", PAND_ITEMS + "def/"])
    assert koppeling.href_ids(h) == ["abc", "def"]
    assert koppeling.href_ids(maak([], dtype=object)) == []
    assert koppeling.href_ids(np.array(PAND_ITEMS + "ghi")) == ["ghi"]  # 0-d array


def test_pand_via_numpy_href(miniwijk):
    mw = miniwijk
    vbo = mw.vbo.copy()
    i = vbo.index[vbo["identificatie"] == mw.vbo_ids["V"]][0]
    hrefs = [np.array(h) for h in vbo["pand.href"]]
    hrefs[vbo.index.get_loc(i)] = np.array([PAND_ITEMS + mw.pand_feature_ids["schuur"]])
    vbo["pand.href"] = pd.Series(hrefs, index=vbo.index, dtype=object)
    r, f = run(mw, vbo=vbo)
    assert f.aantal == 0
    assert per_vhe(r).loc[mw.vhe_nummers["V"], "pand_id"] == mw.pand_ids["schuur"]
