"""Tuinvlak per VHE: perceel − panden, complex-splitsing, voor/achter, QGIS-correcties.

Complex-splitsing ("loodrecht op de BAG-pandscheidingen"): eerst een Voronoi van de
woonzaden op het perceel. Voor elk paar aangrenzende panden die elk precies één woon-vbo
hebben, wordt hun gedeelde grens (de scheidingsmuur, > 1 m) langs zijn eigen richting tot
voorbij het perceel verlengd; het gebied van die twee zaden wordt dan op die lijn opnieuw
verdeeld. Zo vallen de sneden precies op de muren, ook bij ongelijke breedtes of een uitbouw.
Panden met meerdere woon-vbo's (BAG slaat een rij soms als één pand op) en zaden zonder
gedeelde muur houden de Voronoi-grens. Correctie in QGIS (`invoer/tuincorrecties.gpkg`)
blijft de terugvaloptie.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from itertools import combinations
from pathlib import Path
from typing import Literal

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, nearest_points, split

from tuinscan.config import Config
from tuinscan.fouten import Foutenlijst
from tuinscan.tobias import normaliseer_bag_id

log = logging.getLogger(__name__)

STAP = "tuinvlak"
DELEN = ("voor", "achter")
GEEN_VOORZIJDE = "voorzijde niet bepaald (geen wegdeel binnen zoekafstand); alles als achter"
CORRECTIE_LAAG = "tuinvlak"
GEDEELD = "gedeelde tuin (meerdere woningen in één pand)"
_MIN_AREA = 1e-6
MIN_MUUR_M = 1.0  # kortere gedeelde grens telt niet als scheidingsmuur
STAPEL_M = 1.0  # woon-vbo's in één pand dichter bij elkaar → één zaad (gestapeld)
_MUUR_TOLERANTIE_M = 0.1  # kleine gaten/overlap tussen BAG-panden

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


def maak_geldig(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Ongeldige vlakken (bijv. een strik) via make_valid; alleen de vlakdelen blijven."""
    geoms = gdf.geometry
    ongeldig = geoms.notna() & ~geoms.is_valid
    if not ongeldig.any():
        return gdf
    log.warning("%d ongeldige geometrie(ën) hersteld met make_valid", int(ongeldig.sum()))
    nieuw = [_vlakken(shapely.make_valid(g)) if o else g
             for g, o in zip(geoms, ongeldig, strict=True)]  # fmt: skip
    return gdf.set_geometry(gpd.GeoSeries(nieuw, index=gdf.index, crs=gdf.crs))


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


def _stapel(punten: list[Point]) -> list[int]:
    """Clusternummer per punt: punten binnen STAPEL_M van een clusterbegin horen erbij."""
    begin: list[Point] = []
    uit = []
    for p in punten:
        for k, b in enumerate(begin):
            if p.distance(b) < STAPEL_M:
                uit.append(k)
                break
        else:
            begin.append(p)
            uit.append(len(begin) - 1)
    return uit


def woon_zaden(
    perceel: BaseGeometry,
    vbo_woon: gpd.GeoDataFrame,
    panden: gpd.GeoDataFrame,
    vbo_id_veld: str,
    verplicht: Iterable[str] = (),
    eigen_pand: Mapping[str, object] | None = None,
) -> gpd.GeoDataFrame:
    """Eén zaad per woon-vbo op het perceel (alle woon-vbo's, niet alleen onze VHE's).

    Zaad = centroïde van het pand als dat pand precies één woon-vbo bevat, anders het
    vbo-punt; woon-vbo's in één pand die dichter dan STAPEL_M bij elkaar liggen (gestapelde
    woningen) krijgen samen één zaad (hun gemiddelde). `vbo_woon` moet alle woon-vbo's
    bevatten (telling per pand is ruimtelijk). `verplicht`: vbo-id's die altijd meedoen.
    `eigen_pand`: vbo-id → indexlabel in `panden` uit de koppeling (gaat boven ruimtelijk).
    Kolommen: `vbo_id`, `pand` (label of None), `enkel` (pand heeft één woon-vbo),
    `gedeeld` (aantal woningen op dit zaad), geometry (zaad).
    """
    eigen_pand = dict(eigen_pand or {})
    leeg = gpd.GeoDataFrame({"vbo_id": [], "pand": [], "enkel": [], "gedeeld": []},
                            geometry=[], crs=vbo_woon.crs)  # fmt: skip
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

    ruimtelijk = _eerste_pand(kand.geometry, panden)
    labels = [eigen_pand.get(vid, r) for vid, r in zip(ids, ruimtelijk, strict=True)]

    def n_woon(p) -> int:  # alle woon-vbo's in het pand, ook van buurpercelen
        raak = vbo_woon.sindex.query(panden.geometry.at[p], predicate="intersects")
        binnen = {i for i in vbo_woon[vbo_id_veld].iloc[raak].map(normaliseer_bag_id).dropna()
                  if eigen_pand.get(i, p) == p}  # fmt: skip
        return len(binnen | {vid for vid, q in zip(ids, labels, strict=True) if q == p})

    telling = {p: n_woon(p) for p in set(labels) - {None}}
    df = pd.DataFrame({"vbo_id": list(ids), "pand": labels, "punt": list(kand.geometry)})
    df["enkel"] = [p is not None and telling[p] == 1 for p in labels]
    zaad: list = [None] * len(df)
    gedeeld = [1] * len(df)
    for _, groep in df.groupby(df["pand"].map(lambda p: ("p", p)), sort=False):
        pos = [df.index.get_loc(i) for i in groep.index]
        if groep["enkel"].iloc[0]:
            zaad[pos[0]] = panden.geometry.at[groep["pand"].iloc[0]].centroid
            continue
        clusters = _stapel(list(groep["punt"]))
        for k in set(clusters):
            leden = [pos[j] for j, c in enumerate(clusters) if c == k]
            m = MultiPoint([df["punt"].iat[j] for j in leden]).centroid
            for j in leden:
                zaad[j] = m
                gedeeld[j] = len(leden)
    df["gedeeld"] = gedeeld
    return gpd.GeoDataFrame(df.drop(columns="punt"), geometry=zaad, crs=vbo_woon.crs)


def _langste_lijn(geom: BaseGeometry) -> LineString | None:
    lijnen = [g for g in shapely.get_parts(geom) if g.geom_type == "LineString" and g.length > 0]
    if not lijnen:
        return None
    samen = linemerge(MultiLineString(lijnen)) if len(lijnen) > 1 else lijnen[0]
    return max(shapely.get_parts(samen), key=lambda g: g.length)


def _scheidingsmuur(a: BaseGeometry, b: BaseGeometry) -> LineString | None:
    """Gedeelde grens van twee aangrenzende panden (≥ MIN_MUUR_M), of None."""
    lijn = _langste_lijn(shapely.intersection(a.boundary, b.boundary))
    if lijn is None or lijn.length < MIN_MUUR_M:  # niet exact gedeelde hoekpunten
        lijn = _langste_lijn(shapely.intersection(a.boundary, b.buffer(_MUUR_TOLERANTIE_M)))
        if lijn is not None and len(lijn.coords) > 2:  # haakjes van de buffer aan de uiteinden
            c = list(lijn.coords)
            if Point(c[0]).distance(Point(c[1])) <= 2 * _MUUR_TOLERANTIE_M:
                c = c[1:]
            if len(c) > 2 and Point(c[-1]).distance(Point(c[-2])) <= 2 * _MUUR_TOLERANTIE_M:
                c = c[:-1]
            lijn = LineString(c)
    return lijn if lijn is not None and lijn.length >= MIN_MUUR_M else None


def _halfvlakken(muur: LineString, doos: Polygon) -> tuple[BaseGeometry, BaseGeometry] | None:
    """Twee helften van `doos`, gesneden door de muur verlengd langs zijn eigen richting."""
    x0, y0, x1, y1 = doos.bounds
    lengte = 2 * math.hypot(x1 - x0, y1 - y0)
    c = list(muur.coords)

    def verleng(p, q):  # voorbij p, in richting q → p
        dx, dy = p[0] - q[0], p[1] - q[1]
        d = math.hypot(dx, dy)
        return (p[0] + dx / d * lengte, p[1] + dy / d * lengte)

    lijn = LineString([verleng(c[0], c[1]), *c, verleng(c[-1], c[-2])])
    delen = list(split(doos, lijn).geoms)
    return (delen[0], delen[1]) if len(delen) == 2 else None


def _muurcorrectie(cellen: dict, info: dict, panden: gpd.GeoDataFrame, perceel) -> dict:
    """Verdeel per paar aangrenzende enkel-panden hun twee cellen opnieuw op de muurlijn."""
    wand = [(k, p) for k, (p, enkel) in info.items() if enkel and p is not None]
    if len(wand) < 2:
        return cellen
    x0, y0, x1, y1 = shapely.union_all([perceel, *cellen.values()]).bounds
    doos = shapely.box(x0, y0, x1, y1).buffer(10, join_style="mitre")
    for (ka, pa), (kb, pb) in combinations(wand, 2):
        ga, gb = panden.geometry.at[pa], panden.geometry.at[pb]
        if pa == pb or ga.distance(gb) > _MUUR_TOLERANTIE_M:
            continue
        muur = _scheidingsmuur(ga, gb)
        helften = _halfvlakken(muur, doos) if muur is not None else None
        if helften is None:
            continue
        ha = max(helften, key=lambda h: h.intersection(ga).area)
        hb = max(helften, key=lambda h: h.intersection(gb).area)
        if ha is hb:
            continue
        samen = cellen[ka].union(cellen[kb])
        na, nb = _vlakken(samen.intersection(ha)), _vlakken(samen.intersection(hb))
        if na.is_empty or nb.is_empty:
            continue
        cellen[ka], cellen[kb] = na, nb
    return cellen


def splits_perceel(
    perceel_geom: BaseGeometry,
    zaden: gpd.GeoDataFrame,
    panden: gpd.GeoDataFrame,
    max_diepte_m: float,
) -> dict[str, BaseGeometry]:
    """Tuinvlak per vbo-id: cel van het zaad (Voronoi, op scheidingsmuren gecorrigeerd)
    ∩ perceel − alle panden ∩ buffer(eigen pand of zaad, max_diepte_m).
    Eén (samengevoegd) zaad → het hele perceel. Zaden op hetzelfde punt delen hun cel."""
    if not perceel_geom.is_valid:
        perceel_geom = _vlakken(shapely.make_valid(perceel_geom))
    if panden.empty:
        vrij = perceel_geom
    else:
        raak = panden.sindex.query(perceel_geom, predicate="intersects")
        bebouwd = shapely.union_all(panden.geometry.values[raak]) if len(raak) else None
        vrij = perceel_geom.difference(bebouwd) if bebouwd is not None else perceel_geom

    punten = [(z.x, z.y) for z in zaden.geometry]
    info: dict[tuple[float, float], tuple[object, bool]] = {}
    for punt, p, enkel in zip(punten, zaden["pand"], zaden["enkel"], strict=True):
        info.setdefault(punt, (p, bool(enkel)))
    uniek = list(info)
    if len(uniek) <= 1:
        cellen = {p: perceel_geom for p in uniek}
    else:
        vor = shapely.voronoi_polygons(MultiPoint(uniek), extend_to=perceel_geom, ordered=True)
        cellen = dict(zip(uniek, shapely.get_parts(vor), strict=True))
        cellen = _muurcorrectie(cellen, info, panden, perceel_geom)

    uit: dict[str, BaseGeometry] = {}
    for rij, punt in zip(zaden.itertuples(), punten, strict=True):
        basis = panden.geometry.at[rij.pand] if rij.pand is not None else rij.geometry
        tuin = vrij.intersection(cellen[punt]).intersection(basis.buffer(max_diepte_m))
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


def pas_correcties_toe(
    tuinen: gpd.GeoDataFrame, correcties_pad: Path, vhes: pd.DataFrame
) -> gpd.GeoDataFrame:
    """Vervang automatische tuinvlakken door QGIS-correcties per (vhe_nummer, deel).

    Laag `tuinvlak` met kolommen `vhe_nummer, deel`. "In deze ronde" = in `vhes` (de
    VHE-tabel van de ronde), dus een correctie kan ook een tuin geven aan een VHE waarvan de
    automatische tuin mislukte; adres/wijk/complex komen uit `vhes`. Gecorrigeerde rijen
    krijgen `methode="handmatig"`. Geometrie via make_valid; zonder CRS → EPSG:28992
    (waarschuwing); andere CRS → herprojectie. Correcties voor VHE's buiten deze ronde
    worden met een waarschuwing genegeerd.
    """
    correcties_pad = Path(correcties_pad)
    if not correcties_pad.exists():
        return tuinen
    corr = gpd.read_file(correcties_pad, layer=CORRECTIE_LAAG)
    if corr.crs is None:
        log.warning("%s heeft geen CRS; aangenomen EPSG:28992", correcties_pad.name)
        corr = corr.set_crs(28992)
    elif corr.crs.to_epsg() != 28992:
        corr = corr.to_crs(28992)
    geom_kol = tuinen.geometry.name
    rijen = tuinen.to_dict("records")
    attrs = {"adres", "wijk", "complex"}
    ronde = {str(r["vhe_nummer"]): {k: r.get(k) for k in attrs}
             for r in vhes.to_dict("records")}  # fmt: skip
    for vhe, deel, geom in zip(corr["vhe_nummer"], corr["deel"], corr.geometry, strict=True):
        vhe, deel = _vhe_tekst(vhe), str(deel).strip()
        if vhe not in ronde:
            log.warning("tuincorrectie voor VHE %s genegeerd: niet in deze ronde", vhe)
            continue
        geom = _vlakken(shapely.make_valid(geom)) if geom is not None else None
        if deel not in DELEN or geom is None or geom.is_empty:
            log.warning("tuincorrectie voor VHE %s genegeerd: ongeldig deel of geometrie", vhe)
            continue
        treffers = [r for r in rijen if str(r["vhe_nummer"]) == vhe and r["deel"] == deel]
        if treffers:
            dubbel = {id(r) for r in treffers[1:]}
            rijen = [r for r in rijen if id(r) not in dubbel]
            doel = treffers[0]
        else:
            doel = {k: None for k in tuinen.columns} | {"vhe_nummer": vhe, "deel": deel}
            rijen.append(doel)
        doel.update({k: v for k, v in ronde[vhe].items() if k in tuinen.columns})
        doel[geom_kol] = geom
        doel["methode"] = "handmatig"
    return gpd.GeoDataFrame(
        pd.DataFrame(rijen, columns=tuinen.columns), geometry=geom_kol, crs=28992
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

    Verwacht geldige geometrieën (`maak_geldig`, zoals `stap_tuinen` doet).

    Kolommen: vhe_nummer, deel, methode ∈ {perceel, gesplitst}, adres, wijk, complex, geometry.
    """
    v = cfg.pdok.velden
    eigen = set(gekoppeld["bag_vbo_id"].astype(str))
    vbo_ids = vbo[v["vbo_id"]].map(normaliseer_bag_id)
    woon = vbo[vbo[v["vbo_gebruiksdoel"]].map(lambda w: is_woon(w, v["woonfunctie"]))
               | vbo_ids.isin(eigen)]  # fmt: skip
    pand_label: dict[str, object] = {}
    for label, pid in panden[v["pand_id"]].astype(str).items():
        pand_label.setdefault(pid, label)
    # één bron voor "eigen pand": de koppeling (zaad, buffer én voor/achter)
    paren = zip(gekoppeld["bag_vbo_id"], gekoppeld["pand_id"], strict=True)
    eigen_pand = {str(b): pand_label.get(str(p)) for b, p in paren}
    perceel_geom: dict[str, BaseGeometry] = {}
    for pid, g in zip(percelen[v["perceel_id"]].astype(str), percelen.geometry, strict=True):
        perceel_geom.setdefault(pid, g)

    rijen, geoms = [], []
    for perceel_id, groep in gekoppeld.groupby("perceel_id", sort=False):
        try:
            perceel = perceel_geom[str(perceel_id)]
            zaden = woon_zaden(perceel, woon, panden, v["vbo_id"],
                               verplicht=groep["bag_vbo_id"], eigen_pand=eigen_pand)  # fmt: skip
            cellen = splits_perceel(perceel, zaden, panden, cfg.tuin.max_diepte_m)
            n_zaden = len({(g.x, g.y) for g in zaden.geometry})
            methode = "perceel" if n_zaden == 1 else "gesplitst"
            gedeeld = dict(zip(zaden["vbo_id"], zaden["gedeeld"], strict=True))
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
                if gedeeld.get(str(rij.bag_vbo_id), 1) > 1:
                    fouten.voeg_toe(vhe, STAP, GEDEELD)
                pand = panden.geometry.at[eigen_pand[str(rij.bag_vbo_id)]]
                delen = deel_voor_achter(
                    tuin, pand, rij.geometry, wegdelen,
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
