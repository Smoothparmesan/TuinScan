"""Stapfuncties die de modules aan elkaar knopen (tuinen; later score/rapport/alles)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date

import geopandas as gpd
import pandas as pd

from tuinscan import tobias
from tuinscan.config import Config, parameterset_hash
from tuinscan.download import lees_vector
from tuinscan.fouten import Foutenlijst
from tuinscan.koppeling import _href_ids, koppel, koppelpercentage
from tuinscan.mappen import Mappen
from tuinscan.pdok.cache import vector_pad
from tuinscan.rasters import bouw_tuinrasters, bronnen_uit_cache, overhang_zone
from tuinscan.score import aggregeer_delen, bereken_score
from tuinscan.signalen import bereken_signalen
from tuinscan.tuinvlak import STAP, maak_geldig, maak_tuinen, pas_correcties_toe

log = logging.getLogger(__name__)

CORRECTIES = "tuincorrecties.gpkg"
TUINEN_LAAG = "tuinvlak"
TUIN_KOLOMMEN = ["vhe_nummer", "deel", "methode", "oppervlak_m2", "adres", "wijk", "complex"]
WAARSCHUW_PCT = 90


class StapFout(Exception):
    """Een stap kan niet beginnen (bijv. ontbrekende cache); de melding zegt wat te doen."""


@dataclass(frozen=True)
class TuinenRapport:
    n_vhe: int
    n_tuin: int
    percentage: float


def _lees_cache(mappen: Mappen, sleutel: str) -> gpd.GeoDataFrame:
    pad = vector_pad(mappen.cache, sleutel)
    if not pad.exists():
        raise StapFout(f"{pad} ontbreekt; draai eerst 'tuinscan download'")
    gdf = lees_vector(pad)
    if gdf.crs is None:
        gdf = gdf.set_crs(28992)
    elif gdf.crs.to_epsg() != 28992:
        gdf = gdf.to_crs(28992)
    return maak_geldig(gdf)  # één keer, vóór koppeling en tuinvlak


def _pct(p: float) -> str:
    return f"{round(p, 1):g}"


def _schrijf_ronde_json(cfg: Config, mappen: Mappen) -> None:
    t = cfg.tobias
    ronde = {
        "datum": date.today().isoformat(),
        "gebied": {"naam": cfg.gebied.naam, "bbox_rd": list(cfg.gebied.bbox_rd)},
        "filters": t.filters,
        "kolommen": tobias.laad_mapping(mappen.invoer / t.kolommen_bestand),
        "parameterset": parameterset_hash(cfg),
    }
    (mappen.ronde_dir / "ronde.json").write_text(
        json.dumps(ronde, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def stap_tuinen(cfg: Config, mappen: Mappen) -> TuinenRapport:
    """VHE's → koppeling → tuinvlak (split, voor/achter, correcties) → tuinen.gpkg."""
    fouten = Foutenlijst()
    vhes = tobias.lees_vhes(cfg, mappen, fouten)
    vbo, panden, percelen, wegdelen = (
        _lees_cache(mappen, k) for k in ("bag_vbo", "bag_pand", "brk_perceel", "bgt_wegdeel")
    )
    gekoppeld = koppel(vhes, vbo, panden, percelen, cfg.pdok.velden, fouten)
    tuinen = maak_tuinen(gekoppeld, vbo, panden, percelen, wegdelen, cfg, fouten)
    tuinen = pas_correcties_toe(tuinen, mappen.invoer / CORRECTIES, vhes)
    tuinen = tuinen[tuinen["deel"].isin(cfg.tuin.delen)].copy()
    tuinen["oppervlak_m2"] = tuinen.geometry.area.round(2)

    min_m2 = cfg.tuin.min_oppervlak_m2
    klein = tuinen["oppervlak_m2"] < min_m2
    for r in tuinen[klein].itertuples():
        fouten.voeg_toe(
            r.vhe_nummer, STAP, f"tuinvlak te klein ({r.deel}: {r.oppervlak_m2:g} m² < {min_m2:g})"
        )
    tuinen = tuinen[~klein]

    met_tuin = set(tuinen["vhe_nummer"])
    verklaard = {r[0] for r in fouten.regels if r[1] == STAP}
    for vhe in gekoppeld["vhe_nummer"].astype(str):
        if vhe not in met_tuin and vhe not in verklaard:
            fouten.voeg_toe(vhe, STAP, f"geen tuinvlak voor deel {', '.join(cfg.tuin.delen)}")

    # Y = alle VHE's uit de export (na filters), ook die al bij het inlezen afvielen.
    tobias_fout = {r[0] for r in fouten.regels if r[1] == tobias.STAP}
    n_vhe = len(set(vhes["vhe_nummer"].astype(str)) | tobias_fout)
    n_tuin = len(met_tuin)
    pct = koppelpercentage(n_vhe, n_tuin)

    rd = mappen.ronde_dir
    rd.mkdir(parents=True, exist_ok=True)
    uit = gpd.GeoDataFrame(
        tuinen[TUIN_KOLOMMEN], geometry=tuinen.geometry.values, crs=28992
    ).reset_index(drop=True)
    gpkg = rd / "tuinen.gpkg"
    gpkg.unlink(missing_ok=True)
    uit.to_file(gpkg, layer=TUINEN_LAAG, driver="GPKG", promote_to_multi=True,
                geometry_type="MultiPolygon")  # fmt: skip
    fouten.schrijf(rd / "foutenlijst.csv")
    _schrijf_ronde_json(cfg, mappen)

    print(f"Koppeling + tuinvlak gelukt voor {n_tuin} van {n_vhe} VHE's ({_pct(pct)} %)")
    if pct < WAARSCHUW_PCT:
        melding = (f"Let op: minder dan {WAARSCHUW_PCT} % gelukt; zie "
                   f"{rd / 'foutenlijst.csv'}")  # fmt: skip
        print(melding)
        log.info(melding)  # print is de waarschuwing; log alleen voor het logbestand
    return TuinenRapport(n_vhe=n_vhe, n_tuin=n_tuin, percentage=pct)


SCORE_STAP = "score"
WAAR = {"ja", "j", "1", "true", "x"}
SIGNAAL_KOLOMMEN = ["S1", "S2", "S3", "S4a", "S5", "S5_toename"]


def _waar(waarde: object) -> bool:
    return str(waarde).strip().lower() in WAAR


def _woon_pand_ids(vbo: gpd.GeoDataFrame, velden: dict[str, str]) -> set[str]:
    """Feature-id's van panden met minstens één verblijfsobject met woonfunctie."""
    ids: set[str] = set()
    for rij in vbo.to_dict("records"):
        doel = rij.get(velden["vbo_gebruiksdoel"])
        if velden["woonfunctie"] in str(doel):
            ids.update(_href_ids(rij.get(velden["vbo_pand_href"])))
    return ids


def _bijgebouw_op_perceel(percelen, panden, woon_ids: set[str]) -> dict[str, bool]:
    """perceel_id -> True als een BAG-pand zonder woon-vbo het perceel doorsnijdt."""
    bij = panden[~panden["feature_id"].astype(str).isin(woon_ids)]
    uit: dict[str, bool] = {}
    for perceel_id, geom in zip(percelen.index, percelen.geometry, strict=True):
        uit[perceel_id] = bool(len(bij) and bij.sindex.query(geom, predicate="intersects").size)
    return uit


def _list_naar_tekst(w) -> str:
    return ",".join(w)


def stap_score(cfg: Config, mappen: Mappen) -> pd.DataFrame:
    """Tuinen → signalen per deel → score per VHE; schrijft parquet's en laag `scores`."""
    rd = mappen.ronde_dir
    gpkg = rd / "tuinen.gpkg"
    vhe_pad = rd / "vhe.parquet"
    if not gpkg.exists() or not vhe_pad.exists():
        raise StapFout(f"{gpkg} of {vhe_pad} ontbreekt; draai eerst 'tuinscan tuinen'")
    tuinen = gpd.read_file(gpkg, layer=TUINEN_LAAG)
    vhes = pd.read_parquet(vhe_pad)
    vbo, panden, percelen, wegdelen, begroeid = (
        _lees_cache(mappen, k)
        for k in ("bag_vbo", "bag_pand", "brk_perceel", "bgt_wegdeel", "bgt_begroeid")
    )
    velden = cfg.pdok.velden
    fouten = Foutenlijst()
    fout_pad = rd / "foutenlijst.csv"
    if fout_pad.exists():
        bestaand = pd.read_csv(fout_pad, dtype=str, keep_default_na=False)
        fouten.regels = [tuple(r) for r in bestaand.itertuples(index=False) if r[1] != SCORE_STAP]

    gekoppeld = koppel(vhes, vbo, panden, percelen, velden, Foutenlijst())
    perceel_van = dict(
        zip(gekoppeld["vhe_nummer"].astype(str), gekoppeld["perceel_id"], strict=True)
    )
    percelen_idx = percelen.set_index(percelen[velden["perceel_id"]].astype(str))
    bijgebouw = _bijgebouw_op_perceel(percelen_idx, panden, _woon_pand_ids(vbo, velden))
    berging = (
        dict(zip(vhes["vhe_nummer"].astype(str), vhes["standaardberging"].map(_waar), strict=True))
        if "standaardberging" in vhes.columns
        else {}
    )

    bronnen = bronnen_uit_cache(mappen)
    s = cfg.signalen
    voetpad = wegdelen[wegdelen[velden["wegdeel_functie"]].isin(cfg.pdok.voetpad_functies)]
    groen = begroeid[begroeid[velden["begroeid_fysiek"]].isin(cfg.pdok.openbaar_groen_fysiek)]

    deel_rijen, vhe_rijen, scores_geom = [], [], []
    tuinen["vhe_nummer"] = tuinen["vhe_nummer"].astype(str)
    for vhe, delen in tuinen.groupby("vhe_nummer", sort=True):
        try:
            negeer = berging.get(vhe, False) and not bijgebouw.get(perceel_van.get(vhe), True)
            per_deel = []
            for r in delen.itertuples():
                zone = overhang_zone(r.geometry, panden, voetpad, groen, s.s3_buffer_m)
                tr = bouw_tuinrasters(r.geometry, zone, bronnen, s.resolutie_m)
                sig = bereken_signalen(tr, cfg, negeer)
                per_deel.append((float(r.oppervlak_m2), sig))
                deel_rijen.append({"vhe_nummer": vhe, "deel": r.deel,
                                   "oppervlak_m2": r.oppervlak_m2, **sig})  # fmt: skip
            agg = aggregeer_delen(per_deel)
            uitkomst = bereken_score(agg, cfg)
        except Exception as e:  # één VHE stopt de stap nooit (N06)
            log.exception("score VHE %s mislukt", vhe)
            fouten.voeg_toe(vhe, SCORE_STAP, f"score mislukt: {e}")
            continue
        eerste = delen.iloc[0]
        lagen = cfg.luchtfoto.lagen
        reden = uitkomst.redenen + [None] * 2
        vhe_rijen.append({
            "vhe_nummer": vhe, "adres": eerste["adres"], "wijk": eerste["wijk"],
            "complex": eerste["complex"], "score": uitkomst.score,
            **{k: agg.get(k) for k in SIGNAAL_KOLOMMEN},
            **{f"bijdrage_{k}": b for k, b in uitkomst.bijdragen.items()},
            "reden_1": reden[0], "reden_2": reden[1],
            "reden_codes": uitkomst.reden_codes, "ontbrekend": uitkomst.ontbrekend,
            "zomer_jaar": lagen.get("zomer_nu", {}).get("jaar"),
            "zomer_vorig_jaar": lagen.get("zomer_vorig", {}).get("jaar"),
            "winter_jaar": lagen.get("winter_nu", {}).get("jaar"),
            "ahn_versie": cfg.ahn.versie, "parameterset": parameterset_hash(cfg),
        })  # fmt: skip
        scores_geom.append(delen.geometry.union_all())

    kol_deel = ["vhe_nummer", "deel", "oppervlak_m2", *SIGNAAL_KOLOMMEN]
    pd.DataFrame(deel_rijen, columns=kol_deel).to_parquet(rd / "signalen.parquet", index=False)
    scores = pd.DataFrame(vhe_rijen)
    if len(scores):
        sleutel = scores["vhe_nummer"].map(lambda v: (0, int(v), "") if v.isdigit() else (1, 0, v))
        orde = scores.assign(_k=sleutel).sort_values(
            ["wijk", "score", "_k"], ascending=[True, False, True]
        )
        scores["rang_in_wijk"] = (
            orde.groupby("wijk", sort=False).cumcount().add(1).reindex(scores.index)
        )
    else:
        scores["rang_in_wijk"] = pd.Series(dtype=int)
    scores.to_parquet(rd / "scores.parquet", index=False)

    laag = scores.copy()
    laag["reden_codes"] = laag["reden_codes"].map(_list_naar_tekst) if len(laag) else []
    laag["ontbrekend"] = laag["ontbrekend"].map(_list_naar_tekst) if len(laag) else []
    laag = laag[["vhe_nummer", "adres", "wijk", "complex", "score", "rang_in_wijk", "reden_codes",
                 "ontbrekend"]]  # fmt: skip
    gpd.GeoDataFrame(laag, geometry=scores_geom, crs=28992).to_file(
        gpkg, layer="scores", driver="GPKG", promote_to_multi=True
    )
    fouten.schrijf(fout_pad)
    print(f"Score berekend voor {len(scores)} VHE's; {len(fouten.regels)} regels in foutenlijst")
    return scores
