"""Synthetische Tobias-export uit gecachete BAG-data (tot de echte export er is)."""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path

import geopandas as gpd
import pandas as pd

from tuinscan.bestanden import schrijf_atomair
from tuinscan.config import Config
from tuinscan.koppeling import href_ids
from tuinscan.mappen import Mappen
from tuinscan.tobias import normaliseer_bag_id

VOORBEELD_BESTAND = "voorbeeld_export.xlsx"
MIN_M2_PER_WONING = 30.0  # pandoppervlak
AFSTAND_ANDERE_WONING_M = 1.0
KOPPEN = ["Eenheid", "BAG VBO", "Straatnaam", "Huisnr", "Postcode", "Wijknaam", "Complexnr", "Type"]


def _tekst(w: object) -> str:
    return "" if w is None or pd.isna(w) else str(w).strip()


def _huisnr(rij: dict, v: dict[str, str]) -> int | str | None:
    nr = rij.get(v["vbo_huisnummer"])
    if nr is None or pd.isna(nr):
        return None
    try:
        nr_int = int(float(str(nr).strip()))
    except (TypeError, ValueError):
        return None
    letter, toev = _tekst(rij.get(v["vbo_huisletter"])), _tekst(rij.get(v["vbo_toevoeging"]))
    if not letter and not toev:
        return nr_int
    return f"{nr_int}{letter}" + (f"-{toev}" if toev else "")


def maak_voorbeeld_export(cfg: Config, mappen: Mappen, n: int = 200, seed: int = 1) -> Path:
    """Schrijf invoer/voorbeeld_export.xlsx met woon-vbo's die grondgebonden lijken."""
    from tuinscan.stappen import StapFout, _lees_cache

    v = cfg.pdok.velden
    vbo, panden, percelen = (_lees_cache(mappen, k) for k in ("bag_vbo", "bag_pand", "brk_perceel"))
    woon = vbo[vbo[v["vbo_gebruiksdoel"]].map(lambda d: v["woonfunctie"] in str(d))]
    woon = woon[woon.geometry.notna() & ~woon.geometry.is_empty].reset_index(drop=True)
    panden_uniek = panden.drop_duplicates("feature_id")
    pand_idx = panden_uniek.set_index(panden_uniek["feature_id"].astype(str))
    perceel_van = gpd.sjoin(
        woon[[woon.geometry.name]], percelen[[v["perceel_id"], percelen.geometry.name]],
        how="left", predicate="within",
    )  # fmt: skip
    perceel_van = perceel_van[~perceel_van.index.duplicated()][v["perceel_id"]]

    pand_van = [
        [p for p in href_ids(rij.get(v["vbo_pand_href"])) if p in pand_idx.index]
        for rij in woon.to_dict("records")
    ]
    ontbreekt = [i for i, p in enumerate(pand_van) if not p]
    if ontbreekt:  # terugval: ruimtelijk, het pand dat het punt bevat
        sj = gpd.sjoin(woon.iloc[ontbreekt][[woon.geometry.name]], panden[["feature_id",
                       panden.geometry.name]], how="inner", predicate="within")  # fmt: skip
        for i, fid in sj["feature_id"].astype(str).items():
            pand_van[i] = [fid]
    woon_per_pand = Counter(p[0] for p in pand_van if p)

    kandidaten = []
    for i, rij in enumerate(woon.to_dict("records")):
        geom = woon.geometry.iloc[i]
        andere = woon.sindex.query(geom.buffer(AFSTAND_ANDERE_WONING_M), predicate="intersects")
        if len(andere) > 1:
            continue  # een tweede woning vlakbij: stapeling of appartement
        bag_id = normaliseer_bag_id(rij.get(v["vbo_id"]))
        hn = _huisnr(rij, v)
        if not pand_van[i] or bag_id is None or hn is None:
            continue
        pand = pand_idx.loc[pand_van[i][0]]
        aantal = max(float(pand.get(v["pand_aantal_vbo"]) or 1), 1.0)
        # grondgebonden: het pand heeft precies één woning (rijwoning: één pand per huis)
        if woon_per_pand[pand_van[i][0]] != 1 or aantal != 1:
            continue
        if pand.geometry.area < MIN_M2_PER_WONING:
            continue
        perceel = perceel_van.get(i)
        kandidaten.append((rij, bag_id, hn, "" if pd.isna(perceel) else str(perceel)))
    if not kandidaten:
        raise StapFout("geen grondgebonden woningen in de cache; draai eerst 'tuinscan download'")

    gekozen = random.Random(seed).sample(kandidaten, min(n, len(kandidaten)))
    gekozen.sort(key=lambda k: k[1])
    rijen = [
        [
            f"VB{i:05d}", int(bag_id), _tekst(rij.get(v["vbo_straat"])),
            hn, _tekst(rij.get(v["vbo_postcode"])), cfg.gebied.naam, perceel,
            "eengezinswoning",
        ]
        for i, (rij, bag_id, hn, perceel) in enumerate(gekozen, start=1)
    ]  # fmt: skip
    pad = mappen.invoer / VOORBEELD_BESTAND
    with schrijf_atomair(pad) as tmp:
        pd.DataFrame(rijen, columns=KOPPEN).to_excel(tmp, index=False)
    return pad
