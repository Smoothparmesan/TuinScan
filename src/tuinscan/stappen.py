"""Stapfuncties die de modules aan elkaar knopen (tuinen; later score/rapport/alles)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date

import geopandas as gpd

from tuinscan import tobias
from tuinscan.config import Config, parameterset_hash
from tuinscan.download import lees_vector
from tuinscan.fouten import Foutenlijst
from tuinscan.koppeling import koppel, koppelpercentage
from tuinscan.mappen import Mappen
from tuinscan.pdok.cache import vector_pad
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
