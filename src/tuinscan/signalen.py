"""Signalen S1, S2, S3, S4a en S5 per tuin(deel) uit luchtfoto's en AHN.

Begroeiing = zomer-NDVI (CIR van hetzelfde jaar, band 1 = NIR, band 2 = rood) boven
`ndvi_drempel` OF zomer-ExG boven `exg_drempel`. "Geldig" = in de tuin (of zone),
ongemaskeerd in elke gebruikte invoer en geen schaduw. Aandelen gaan over geldige
pixels (geen geldige pixel -> None); oppervlakken (S3, S4a) tellen alleen geldige pixels.
Alle drempels komen uit `cfg.signalen`.
"""

from __future__ import annotations

import logging

import numpy as np
from affine import Affine
from rasterio.features import rasterize, shapes

from tuinscan.config import Config
from tuinscan.rasters import Raster, TuinRasters

log = logging.getLogger(__name__)

SIGNAAL_SLEUTELS = ("S1", "S2", "S3", "S4a", "S5", "S5_toename")


def _data(r) -> np.ndarray:
    return np.ma.filled(np.ma.asarray(r, dtype=np.float64), 0.0)


def _deel(teller: np.ndarray, noemer: np.ndarray) -> np.ndarray:
    uit = np.zeros(np.shape(teller), dtype=np.float64)
    np.divide(teller, noemer, out=uit, where=noemer != 0)
    return uit


def exg(rgb) -> np.ndarray:
    """Excess Green 2g − r − b op chromatische coördinaten (R+G+B = 0 -> 0)."""
    r, g, b = _data(rgb)[:3]
    s = r + g + b
    return _deel(2 * g - r - b, s)


def ndvi_cir(cir) -> np.ndarray:
    """NDVI uit CIR: band 1 = NIR, band 2 = rood; (b1 − b2)/(b1 + b2), noemer 0 -> 0."""
    nir, rood = _data(cir)[:2]
    return _deel(nir - rood, nir + rood)


def schaduw(rgb, helderheid: float) -> np.ndarray:
    """True waar de gemiddelde helderheid van R, G, B onder `helderheid` ligt."""
    return _data(rgb)[:3].mean(axis=0) < helderheid


def vegetatie(zomer, cir, cfg: Config) -> np.ndarray:
    """Begroeiing: NDVI(cir) > ndvi_drempel OF ExG(zomer) > exg_drempel; NDVI-deel vervalt
    als `cir` None is."""
    s = cfg.signalen
    veg = exg(zomer) > s.exg_drempel
    if cir is not None:
        veg |= ndvi_cir(cir) > s.ndvi_drempel
    return veg


def _ongemaskeerd(*rasters: Raster | None) -> np.ndarray:
    """True waar alle gegeven (niet-None) rasters in elke band een waarde hebben."""
    rasters = [r for r in rasters if r is not None]
    ok = np.ones(rasters[0].shape[1:], dtype=bool)
    for r in rasters:
        ok &= ~np.ma.getmaskarray(r).any(axis=0)
    return ok


def _aandeel(teller: np.ndarray, geldig: np.ndarray) -> float | None:
    n = int(geldig.sum())
    return None if n == 0 else float((teller & geldig).sum()) / n


def _pixel_m2(tr: TuinRasters) -> float:
    return abs(tr.transform.a * tr.transform.e)


def _twee_jaar(tr: TuinRasters, cfg: Config):
    """(geldig, veg_nu, veg_vorig) over dezelfde pixels in beide zomers, of None.

    CIR telt alleen mee als beide jaren CIR hebben: anders zou NDVI het groen in één
    jaar wel en in het andere niet zien en de toename opblazen.
    """
    if tr.zomer_nu is None or tr.zomer_vorig is None:
        return None
    met_cir = tr.cir_nu is not None and tr.cir_vorig is not None
    cir_nu, cir_vorig = (tr.cir_nu, tr.cir_vorig) if met_cir else (None, None)
    h = cfg.signalen.schaduw_helderheid
    geldig = (
        tr.tuin_masker
        & _ongemaskeerd(tr.zomer_nu, tr.zomer_vorig, cir_nu, cir_vorig)
        & ~schaduw(tr.zomer_nu, h)
        & ~schaduw(tr.zomer_vorig, h)
    )
    return geldig, vegetatie(tr.zomer_nu, cir_nu, cfg), vegetatie(tr.zomer_vorig, cir_vorig, cfg)


def _nu_met_hoogte(tr: TuinRasters, cfg: Config):
    """(ongemaskeerd, geen schaduw, veg_nu, ndsm) voor dit jaar; None zonder zomer of ndsm."""
    if tr.zomer_nu is None or tr.ndsm is None:
        return None
    ok = _ongemaskeerd(tr.zomer_nu, tr.cir_nu, tr.ndsm)
    licht = ~schaduw(tr.zomer_nu, cfg.signalen.schaduw_helderheid)
    return ok, licht, vegetatie(tr.zomer_nu, tr.cir_nu, cfg), _data(tr.ndsm)[0]


def s1_groentoename(tr: TuinRasters, cfg: Config) -> float | None:
    """Groen-aandeel zomer_nu − groen-aandeel zomer_vorig; None als een jaar ontbreekt."""
    jaren = _twee_jaar(tr, cfg)
    if jaren is None:
        return None
    geldig, veg_nu, veg_vorig = jaren
    nu, vorig = _aandeel(veg_nu, geldig), _aandeel(veg_vorig, geldig)
    return None if nu is None else nu - vorig


def s2_hoge_begroeiing(tr: TuinRasters, cfg: Config) -> float | None:
    """Aandeel geldige tuinpixels met begroeiing en ndsm > s2_hoogte_m."""
    nu = _nu_met_hoogte(tr, cfg)
    if nu is None:
        return None
    ok, licht, veg, ndsm = nu
    return _aandeel(veg & (ndsm > cfg.signalen.s2_hoogte_m), tr.tuin_masker & ok & licht)


def s3_overhang(tr: TuinRasters, cfg: Config) -> float | None:
    """m² in de overhangzone met begroeiing en ndsm > s3_hoogte_m; lege zone -> 0.0."""
    if not tr.zone_masker.any():
        return 0.0
    nu = _nu_met_hoogte(tr, cfg)
    if nu is None:
        return None
    ok, licht, veg, ndsm = nu
    zone = tr.zone_masker & ok
    if not zone.any():
        return None  # zone ligt volledig buiten de beschikbare beelden
    hoog = zone & licht & veg & (ndsm > cfg.signalen.s3_hoogte_m)
    return float(hoog.sum()) * _pixel_m2(tr)


def _regio_labels(kandidaat: np.ndarray) -> tuple[np.ndarray, int]:
    """8-verbonden regio's van `kandidaat` als labelraster (0 = geen) en aantal regio's."""
    eenheid = Affine.identity()
    geoms = [g for g, _ in shapes(kandidaat.astype(np.uint8), mask=kandidaat,
                                  connectivity=8, transform=eenheid)]  # fmt: skip
    if not geoms:
        return np.zeros(kandidaat.shape, dtype=np.int32), 0
    labels = rasterize(
        ((g, i) for i, g in enumerate(geoms, start=1)), out_shape=kandidaat.shape,
        transform=eenheid, fill=0, dtype="int32",
    )  # fmt: skip
    return labels, len(geoms)


def s4a_hoog_object(tr: TuinRasters, cfg: Config, negeer_berging: bool) -> float | None:
    """Totaal m² van 8-verbonden niet-groene regio's met ndsm > s4a_hoogte_m in de tuin.

    Elke regio ≥ s4a_min_m2; met ndsm_vorig telt een regio alleen als de gemiddelde
    vorige hoogte < s4a_vorige_max_m (regio zonder vorige waarden telt mee, net als
    zonder vorige AHN). Bij `negeer_berging` valt de grootste regio ≤
    s4a_standaardberging_max_m2 weg.
    """
    s = cfg.signalen
    nu = _nu_met_hoogte(tr, cfg)
    if nu is None:
        return None
    ok, licht, veg, ndsm = nu
    tuin = tr.tuin_masker & ok
    if not tuin.any():
        return None
    labels, n = _regio_labels(tuin & licht & ~veg & (ndsm > s.s4a_hoogte_m))
    if n == 0:
        return 0.0
    m2 = np.bincount(labels.ravel(), minlength=n + 1)[1:] * _pixel_m2(tr)
    telt = m2 >= s.s4a_min_m2
    if tr.ndsm_vorig is not None:
        vorig_ok = ~np.ma.getmaskarray(tr.ndsm_vorig)[0]
        lab = labels.ravel()
        aantal = np.bincount(lab, weights=vorig_ok.ravel(), minlength=n + 1)[1:]
        som = np.bincount(lab, weights=(_data(tr.ndsm_vorig)[0] * vorig_ok).ravel(),
                          minlength=n + 1)[1:]  # fmt: skip
        gemiddeld = _deel(som, aantal)
        telt &= (aantal == 0) | (gemiddeld < s.s4a_vorige_max_m)
    regio = m2[telt]
    if negeer_berging:
        berging = regio[regio <= s.s4a_standaardberging_max_m2]
        if berging.size:
            regio = np.delete(regio, np.flatnonzero(regio == berging.max())[0])
    return float(regio.sum())


def s5_verharding(tr: TuinRasters, cfg: Config) -> tuple[float | None, float | None]:
    """(aandeel niet-groen in beide zomers, niet-groen-aandeel nu − vorig); zonder vorig
    jaar (None, None)."""
    jaren = _twee_jaar(tr, cfg)
    if jaren is None:
        return None, None
    geldig, veg_nu, veg_vorig = jaren
    beide = _aandeel(~veg_nu & ~veg_vorig, geldig)
    if beide is None:
        return None, None
    return beide, _aandeel(~veg_nu, geldig) - _aandeel(~veg_vorig, geldig)


def bereken_signalen(tr: TuinRasters, cfg: Config, negeer_berging: bool) -> dict[str, float | None]:
    """Alle signalen; een fout in één signaal geeft None voor die sleutel (en een logregel).

    `negeer_berging` = VHE heeft standaardberging EN geen BAG-bijgebouw op het perceel.
    """
    uit: dict[str, float | None] = dict.fromkeys(SIGNAAL_SLEUTELS)
    berekeningen = {
        "S1": lambda: s1_groentoename(tr, cfg),
        "S2": lambda: s2_hoge_begroeiing(tr, cfg),
        "S3": lambda: s3_overhang(tr, cfg),
        "S4a": lambda: s4a_hoog_object(tr, cfg, negeer_berging),
    }
    for sleutel, bereken in berekeningen.items():
        try:
            uit[sleutel] = bereken()
        except Exception:
            log.exception("signaal %s mislukt", sleutel)
    try:
        uit["S5"], uit["S5_toename"] = s5_verharding(tr, cfg)
    except Exception:
        log.exception("signaal S5 mislukt")
    return uit
