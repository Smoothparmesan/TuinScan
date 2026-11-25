"""Koppeling VHE → BAG-verblijfsobject → pand → BRK-perceel.

Veldnamen komen uit `cfg.pdok.velden`; `feature_id` is de kolom die `pdok.ogc` zelf zet.
"""

from __future__ import annotations

import json
import logging

import geopandas as gpd
import pandas as pd

from tuinscan.fouten import Foutenlijst
from tuinscan.tobias import BAG_HINT, normaliseer_bag_id

log = logging.getLogger(__name__)

STAP = "koppeling"
FEATURE_ID = "feature_id"
NIET_GEVONDEN = "BAG-verblijfsobject niet gevonden in werkgebied"
DUBBEL = "meerdere VHE's op hetzelfde verblijfsobject"
UIT_KOLOMMEN = ("pand_id", "perceel_id")


def href_ids(waarde: object) -> list[str]:
    """Feature-id's (laatste URL-segment) uit een `pand.href`-waarde."""
    if waarde is None or (isinstance(waarde, float) and waarde != waarde):
        return []
    if not isinstance(waarde, str) and hasattr(waarde, "tolist"):  # numpy-array / Series
        waarde = waarde.tolist()
    if isinstance(waarde, str):
        s = waarde.strip()
        if s.startswith("["):
            waarde = json.loads(s)
        else:
            waarde = [s] if s else []
    if not isinstance(waarde, (list, tuple)):
        raise ValueError(f"onverwachte pand-verwijzing {waarde!r}")
    return [str(h).rstrip("/").rsplit("/", 1)[-1] for h in waarde if str(h).strip()]


def _bevat(gdf: gpd.GeoDataFrame, punt) -> list:
    """Indexlabels van features die het punt bevatten (rand telt mee)."""
    if gdf.empty:
        return []
    pos = gdf.sindex.query(punt, predicate="intersects")
    return list(gdf.index[sorted(pos)])


class _Koppelaar:
    def __init__(self, vbo, panden, percelen, velden):
        self.v = velden
        self.panden = panden
        self.percelen = percelen
        self.pand_per_fid: dict[str, object] = {}
        for i, fid in panden[FEATURE_ID].items():
            self.pand_per_fid.setdefault(str(fid), i)
        self.vbo_per_id: dict[str, object] = {}
        for i, w in vbo[velden["vbo_id"]].items():
            bag = normaliseer_bag_id(w)
            if bag is not None:
                self.vbo_per_id.setdefault(bag, i)
        self.vbo = vbo

    def pand(self, vbo_rij) -> object | None:
        punt = vbo_rij.geometry
        kandidaten = [
            self.pand_per_fid[f]
            for f in href_ids(vbo_rij.get(self.v["vbo_pand_href"]))
            if f in self.pand_per_fid
        ]
        if len(kandidaten) == 1:
            return kandidaten[0]
        binnen = set(_bevat(self.panden, punt))
        if kandidaten:
            return next((k for k in kandidaten if k in binnen), kandidaten[0])
        return min(binnen) if binnen else None

    def perceel(self, punt, pand_geom) -> object | None:
        binnen = _bevat(self.percelen, punt)
        if len(binnen) == 1:
            return binnen[0]
        kandidaten = binnen or _bevat(self.percelen, pand_geom)
        if not kandidaten:
            return None
        overlap = self.percelen.geometry.loc[kandidaten].intersection(pand_geom).area
        if not binnen and overlap.max() <= 0:
            return None
        return overlap.idxmax()


def koppel(
    vhes: pd.DataFrame,
    vbo: gpd.GeoDataFrame,
    panden: gpd.GeoDataFrame,
    percelen: gpd.GeoDataFrame,
    velden: dict[str, str],
    fouten: Foutenlijst,
) -> gpd.GeoDataFrame:
    """Eén rij per gekoppelde VHE: VHE-kolommen + pand_id, perceel_id, vbo-punt."""
    k = _Koppelaar(vbo, panden, percelen, velden)
    rijen, geoms, gezien = [], [], {}
    for rij in vhes.to_dict("records"):
        vhe = str(rij["vhe_nummer"])
        try:
            bag = str(rij["bag_vbo_id"])
            if bag in gezien:
                fouten.voeg_toe(vhe, STAP, f"{DUBBEL} {bag} (VHE {gezien[bag]} behouden)")
                continue
            i = k.vbo_per_id.get(bag)
            if i is None:
                reden = f"{NIET_GEVONDEN} ({bag})"
                if bag.endswith("0"):
                    reden += f"; {BAG_HINT}"
                fouten.voeg_toe(vhe, STAP, reden)
                continue
            vbo_rij = vbo.loc[i]
            punt = vbo_rij.geometry
            if punt is None or punt.is_empty:
                fouten.voeg_toe(vhe, STAP, f"verblijfsobject {bag} heeft geen geometrie")
                continue
            p = k.pand(vbo_rij)
            if p is None:
                fouten.voeg_toe(vhe, STAP, f"geen pand gevonden voor verblijfsobject {bag}")
                continue
            q = k.perceel(punt, panden.geometry.loc[p])
            if q is None:
                fouten.voeg_toe(vhe, STAP, f"geen perceel gevonden voor verblijfsobject {bag}")
                continue
        except Exception as e:  # één adres stopt de stap nooit (N06)
            log.exception("koppeling VHE %s mislukt", vhe)
            fouten.voeg_toe(vhe, STAP, f"koppeling mislukt: {e}")
            continue
        gezien[bag] = vhe  # pas na een geslaagde koppeling: eerste gekoppelde VHE wint
        rijen.append({
            **rij,
            "pand_id": str(panden.at[p, velden["pand_id"]]),
            "perceel_id": str(percelen.at[q, velden["perceel_id"]]),
        })  # fmt: skip
        geoms.append(punt)
    kolommen = list(vhes.columns) + [c for c in UIT_KOLOMMEN if c not in vhes.columns]
    return gpd.GeoDataFrame(
        pd.DataFrame(rijen, columns=kolommen), geometry=geoms, crs=vbo.crs or 28992
    )


def koppelpercentage(n_vhe: int, n_gekoppeld: int) -> float:
    """Percentage gekoppelde VHE's (0-100); 0 bij geen VHE's."""
    return 100.0 * n_gekoppeld / n_vhe if n_vhe else 0.0
