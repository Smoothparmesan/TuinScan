"""Tuinvlak per VHE: perceel − panden, complex-splitsing, voor/achter, QGIS-correcties.

Complex-splitsing (afwijking van de spec "loodrecht op de pandscheidingen"): BAG slaat een
rij vaak op als één pand, dus de scheidingsmuren staan niet in de data. De middelloodlijn
tussen buurzaden (Voronoi) geeft bij een gewone rij dezelfde snede en werkt ook bij een
rij-als-één-pand. Correctie in QGIS (`invoer/tuincorrecties.gpkg`) blijft de terugvaloptie.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import MultiPoint, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

from tuinscan.config import Config
from tuinscan.fouten import Foutenlijst
from tuinscan.tobias import normaliseer_bag_id

log = logging.getLogger(__name__)

STAP = "tuinvlak"
DELEN = ("voor", "achter")
GEEN_VOORZIJDE = "voorzijde niet bepaald (geen wegdeel binnen zoekafstand); alles als achter"
CORRECTIE_LAAG = "tuinvlak"
_MIN_AREA = 1e-6

Deel = Literal["voor", "achter"]


def is_woon(gebruiksdoel: object, woonfunctie: str) -> bool:
    """True als een BAG-gebruiksdoel (lijst of tekst, ook 'a,b') de woonfunctie bevat."""
    if isinstance(gebruiksdoel, (list, tuple)):
        return any(str(g).strip() == woonfunctie for g in gebruiksdoel)
    return isinstance(gebruiksdoel, str) and woonfunctie in gebruiksdoel


def _vlakken(geom: BaseGeometry | None) -> BaseGeometry:
    """Alleen de vlakdelen (snijdingen geven soms lijnen of punten mee)."""
    if geom is None or geom.is_empty:
        return Polygon()
    delen = [g for g in shapely.get_parts(geom) if g.geom_type in ("Polygon", "MultiPolygon")]
    delen = [g for g in delen if g.area > _MIN_AREA]
    if not delen:
        return Polygon()
    return delen[0] if len(delen) == 1 else shapely.union_all(delen)


def _eerste_pand(punten: gpd.GeoSeries, panden: gpd.GeoDataFrame) -> list:
    """Indexlabel van het (eerste) pand dat elk punt raakt, of None."""
    uit: list = [None] * len(punten)
    if panden.empty or punten.empty:
        return uit
    i_punt, i_pand = panden.sindex.query(punten.values, predicate="intersects")
    for ip, iq in sorted(zip(i_punt.tolist(), i_pand.tolist(), strict=True)):
        if uit[ip] is None:
            uit[ip] = panden.index[iq]
    return uit


def woon_zaden(
    perceel: BaseGeometry,
    vbo_woon: gpd.GeoDataFrame,
    panden: gpd.GeoDataFrame,
    vbo_id_veld: str,
    verplicht: Iterable[str] = (),
) -> gpd.GeoDataFrame:
    """Eén zaad per woon-vbo op het perceel (alle woon-vbo's, niet alleen onze VHE's).

    Zaad = centroïde van het pand als dat pand precies één woon-vbo bevat, anders het
    vbo-punt. `vbo_woon` moet alle woon-vbo's bevatten (telling per pand is ruimtelijk).
    `verplicht`: vbo-id's die altijd meedoen, ook als hun punt net buiten het perceel ligt.
    Kolommen: `vbo_id`, `pand` (indexlabel in `panden` of None), geometry (zaad).
    """
    leeg = gpd.GeoDataFrame({"vbo_id": [], "pand": []}, geometry=[], crs=vbo_woon.crs)
    if vbo_woon.empty:
        return leeg
    kand = vbo_woon.iloc[sorted(vbo_woon.sindex.query(perceel, predicate="intersects"))]
    ids = kand[vbo_id_veld].map(normaliseer_bag_id)
    ontbreekt = {str(x) for x in verplicht} - set(ids.dropna())
    if ontbreekt:  # alleen dan over alle vbo's normaliseren
        alle = vbo_woon[vbo_id_veld].map(normaliseer_bag_id)
        extra = alle.isin(ontbreekt) & ~vbo_woon.index.isin(kand.index)
        kand = pd.concat([kand, vbo_woon[extra]])
        ids = pd.concat([ids, alle[extra]])
    geldig = (ids.notna() & ~ids.duplicated() & kand.geometry.notna()
              & ~kand.geometry.is_empty).values  # fmt: skip
    kand, ids = kand[geldig], ids[geldig]
    if kand.empty:
        return leeg

    def n_woon(p) -> int:  # ruimtelijk: alle woon-vbo's in het pand, ook van buurpercelen
        raak = vbo_woon.sindex.query(panden.geometry.at[p], predicate="intersects")
        return vbo_woon[vbo_id_veld].iloc[raak].map(normaliseer_bag_id).nunique()

    pand_labels = _eerste_pand(kand.geometry, panden)
    telling = {p: n_woon(p) for p in set(pand_labels) - {None}}
    rijen, geoms = [], []
    for vid, punt, p in zip(ids, kand.geometry, pand_labels, strict=True):
        enig = p is not None and telling[p] == 1
        rijen.append({"vbo_id": vid, "pand": p})
        geoms.append(panden.geometry.at[p].centroid if enig else punt)
    return gpd.GeoDataFrame(rijen, geometry=geoms, crs=vbo_woon.crs)


def splits_perceel(
    perceel_geom: BaseGeometry,
    zaden: gpd.GeoDataFrame,
    panden: gpd.GeoDataFrame,
    max_diepte_m: float,
) -> dict[str, BaseGeometry]:
    """Tuinvlak per vbo-id: Voronoi-cel van het zaad ∩ perceel − alle panden
    ∩ buffer(eigen pand of vbo-punt, max_diepte_m). Eén zaad → het hele perceel."""
    if not perceel_geom.is_valid:
        perceel_geom = shapely.make_valid(perceel_geom)
    if panden.empty:
        vrij = perceel_geom
    else:
        raak = panden.sindex.query(perceel_geom, predicate="intersects")
        bebouwd = shapely.union_all(panden.geometry.values[raak]) if len(raak) else None
        vrij = perceel_geom.difference(bebouwd) if bebouwd is not None else perceel_geom

    punten = [(z.x, z.y) for z in zaden.geometry]
    uniek = list(dict.fromkeys(punten))
    if len(uniek) <= 1:
        cel_per_punt = {p: perceel_geom for p in uniek}
    else:
        vor = shapely.voronoi_polygons(MultiPoint(uniek), extend_to=perceel_geom, ordered=True)
        cel_per_punt = dict(zip(uniek, shapely.get_parts(vor), strict=True))

    uit: dict[str, BaseGeometry] = {}
    for rij, punt in zip(zaden.itertuples(), punten, strict=True):
        basis = panden.geometry.at[rij.pand] if rij.pand is not None else rij.geometry
        tuin = vrij.intersection(cel_per_punt[punt]).intersection(basis.buffer(max_diepte_m))
        uit[rij.vbo_id] = _vlakken(tuin)
    return uit


def _voorvector(pand_geom, vbo_punt, wegdelen, zoekafstand_m) -> tuple[float, float] | None:
    if wegdelen.empty or vbo_punt is None or vbo_punt.is_empty:
        return None
    pos = wegdelen.sindex.query(vbo_punt, predicate="dwithin", distance=zoekafstand_m)
    if not len(pos):
        return None
    kandidaten = wegdelen.geometry.values[pos]
    w = kandidaten[int(shapely.distance(kandidaten, vbo_punt).argmin())]
    c = pand_geom.centroid
    p = nearest_points(w, c)[0]
    dx, dy = p.x - c.x, p.y - c.y
    lengte = math.hypot(dx, dy)
    return None if lengte < 1e-9 else (dx / lengte, dy / lengte)


def deel_voor_achter(
    tuin: BaseGeometry,
    pand_geom: BaseGeometry,
    vbo_punt: Point,
    wegdelen: gpd.GeoDataFrame,
    zoekafstand_m: float,
    *,
    fouten: Foutenlijst | None = None,
    vhe_nummer: str = "",
) -> list[tuple[Deel, BaseGeometry]]:
    """Splits een tuin in voor (wegkant) en achter.

    Voorvector = van de pandcentroïde naar het dichtstbijzijnde punt van het wegdeel
    (elke functie, ook voetpad) dat het dichtst bij het vbo-punt ligt, binnen
    `zoekafstand_m`. Een tuindeel is `voor` als `dot(centroïde − pandcentroïde, voor) > 0`.
    Een deel dat de lijn door de pandcentroïde (loodrecht op de voorvector) kruist, wordt
    op die lijn gesneden; zo krijgt een vrijstaand huis met een tuin rondom ook een
    voortuin. Geen wegdeel → alles `achter` + fout "voorzijde niet bepaald".
    """
    tuin = _vlakken(tuin)
    if tuin.is_empty:
        return []
    f = _voorvector(pand_geom, vbo_punt, wegdelen, zoekafstand_m)
    if f is None:
        if fouten is not None:
            fouten.voeg_toe(vhe_nummer, STAP, GEEN_VOORZIJDE)
        return [("achter", tuin)]
    c = pand_geom.centroid
    x0, y0, x1, y1 = shapely.union(tuin, c).bounds
    lengte = 2 * math.hypot(x1 - x0, y1 - y0) + 1
    n = (-f[1], f[0])
    voorvlak = Polygon([
        (c.x + n[0] * lengte, c.y + n[1] * lengte),
        (c.x + (n[0] + f[0]) * lengte, c.y + (n[1] + f[1]) * lengte),
        (c.x + (f[0] - n[0]) * lengte, c.y + (f[1] - n[1]) * lengte),
        (c.x - n[0] * lengte, c.y - n[1] * lengte),
    ])  # fmt: skip
    uit: list[tuple[Deel, BaseGeometry]] = []
    for deel, ruw in (("voor", tuin.intersection(voorvlak)), ("achter", tuin.difference(voorvlak))):
        geom = _vlakken(ruw)
        if not geom.is_empty:
            uit.append((deel, geom))
    return uit


def _vhe_tekst(w: object) -> str:
    if isinstance(w, float) and w.is_integer():
        return str(int(w))
    return str(w).strip()


def pas_correcties_toe(tuinen: gpd.GeoDataFrame, correcties_pad: Path) -> gpd.GeoDataFrame:
    """Vervang automatische tuinvlakken door QGIS-correcties per (vhe_nummer, deel).

    Laag `tuinvlak` met kolommen `vhe_nummer, deel`. Gecorrigeerde rijen krijgen
    `methode="handmatig"`. Een correctie voor een deel dat automatisch ontbrak, wordt
    toegevoegd. Correcties voor VHE's buiten deze ronde worden met een waarschuwing genegeerd.
    """
    correcties_pad = Path(correcties_pad)
    if not correcties_pad.exists():
        return tuinen
    corr = gpd.read_file(correcties_pad, layer=CORRECTIE_LAAG)
    if tuinen.crs is not None and corr.crs is not None and corr.crs != tuinen.crs:
        corr = corr.to_crs(tuinen.crs)
    geom_kol = tuinen.geometry.name
    rijen = tuinen.to_dict("records")
    bekend = {str(r["vhe_nummer"]) for r in rijen}
    for vhe, deel, geom in zip(corr["vhe_nummer"], corr["deel"], corr.geometry, strict=True):
        vhe, deel = _vhe_tekst(vhe), str(deel).strip()
        if vhe not in bekend:
            log.warning("tuincorrectie voor VHE %s genegeerd: niet in deze ronde", vhe)
            continue
        if deel not in DELEN or geom is None or geom.is_empty:
            log.warning("tuincorrectie voor VHE %s genegeerd: ongeldig deel of geometrie", vhe)
            continue
        treffers = [r for r in rijen if str(r["vhe_nummer"]) == vhe and r["deel"] == deel]
        if treffers:
            dubbel = {id(r) for r in treffers[1:]}
            rijen = [r for r in rijen if id(r) not in dubbel]
            doel = treffers[0]
        else:
            doel = dict(next(r for r in rijen if str(r["vhe_nummer"]) == vhe), deel=deel)
            rijen.append(doel)
        doel[geom_kol] = geom
        doel["methode"] = "handmatig"
    return gpd.GeoDataFrame(
        pd.DataFrame(rijen, columns=tuinen.columns), geometry=geom_kol, crs=tuinen.crs
    )


def maak_tuinen(
    gekoppeld: gpd.GeoDataFrame,
    vbo: gpd.GeoDataFrame,
    panden: gpd.GeoDataFrame,
    percelen: gpd.GeoDataFrame,
    wegdelen: gpd.GeoDataFrame,
    cfg: Config,
    fouten: Foutenlijst,
) -> gpd.GeoDataFrame:
    """Automatische tuinvlakken (voor én achter) voor de `koppel`-uitvoer.

    Kolommen: vhe_nummer, deel, methode ∈ {perceel, gesplitst}, adres, wijk, complex, geometry.
    """
    v = cfg.pdok.velden
    eigen = set(gekoppeld["bag_vbo_id"].astype(str))
    vbo_ids = vbo[v["vbo_id"]].map(normaliseer_bag_id)
    woon = vbo[vbo[v["vbo_gebruiksdoel"]].map(lambda w: is_woon(w, v["woonfunctie"]))
               | vbo_ids.isin(eigen)]  # fmt: skip
    pand_geom = dict(zip(panden[v["pand_id"]].astype(str), panden.geometry, strict=True))
    perceel_geom: dict[str, BaseGeometry] = {}
    for pid, g in zip(percelen[v["perceel_id"]].astype(str), percelen.geometry, strict=True):
        perceel_geom.setdefault(pid, g)

    rijen, geoms = [], []
    for perceel_id, groep in gekoppeld.groupby("perceel_id", sort=False):
        try:
            perceel = perceel_geom[str(perceel_id)]
            zaden = woon_zaden(perceel, woon, panden, v["vbo_id"], verplicht=groep["bag_vbo_id"])
            cellen = splits_perceel(perceel, zaden, panden, cfg.tuin.max_diepte_m)
            methode = "perceel" if len(zaden) == 1 else "gesplitst"
        except Exception as e:  # één perceel stopt de stap nooit (N06)
            log.exception("tuinvlak perceel %s mislukt", perceel_id)
            for vhe in groep["vhe_nummer"]:
                fouten.voeg_toe(str(vhe), STAP, f"tuinvlak mislukt: {e}")
            continue
        for rij in groep.itertuples():
            vhe = str(rij.vhe_nummer)
            try:
                tuin = cellen.get(str(rij.bag_vbo_id))
                if tuin is None or tuin.is_empty:
                    fouten.voeg_toe(vhe, STAP, f"geen tuinvlak op perceel {perceel_id}")
                    continue
                delen = deel_voor_achter(
                    tuin, pand_geom[str(rij.pand_id)], rij.geometry, wegdelen,
                    cfg.tuin.voorzijde_zoekafstand_m, fouten=fouten, vhe_nummer=vhe,
                )  # fmt: skip
            except Exception as e:
                log.exception("tuinvlak VHE %s mislukt", vhe)
                fouten.voeg_toe(vhe, STAP, f"tuinvlak mislukt: {e}")
                continue
            for deel, geom in delen:
                rijen.append({
                    "vhe_nummer": vhe, "deel": deel, "methode": methode,
                    "adres": rij.adres, "wijk": rij.wijk, "complex": rij.complex,
                })  # fmt: skip
                geoms.append(geom)
    kolommen = ["vhe_nummer", "deel", "methode", "adres", "wijk", "complex"]
    return gpd.GeoDataFrame(
        pd.DataFrame(rijen, columns=kolommen), geometry=geoms, crs=panden.crs or 28992
    )
