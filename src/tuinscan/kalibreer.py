"""Stap `kalibreer`: ExG/NDVI per zomerlaag op bekende verharding, daken en openbaar groen.

Doel: de begroeiingsdrempels per luchtfotolaag zo zetten dat wegen en daken niet als groen
tellen en BGT-groen wel. Gemeten wordt alleen op zonbeschenen pixels (geen schaduw in de
RGB-zomerfoto van hetzelfde jaar) binnen het werkgebied (`gebied.bbox_rd`). Klassen komen uit
de cache: BGT-wegdeel (`pdok.rijbaan_functies`, `pdok.voetpad_functies`), BAG-panden (daken)
en BGT-begroeid (`pdok.openbaar_groen_fysiek`); elk vlak wordt eerst `KRIMP_M` naar binnen
verkleind, zodat randen en scheefstand van de foto de meting weinig vervuilen.

Het voorstel per laag is het midden tussen p90 van de hardste niet-groene klasse (rijbaan of
dak) en p25 van BGT-groen. De score-schaal verandert niet; alleen het masker.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import shapely
from rasterio.enums import Resampling
from rasterio.features import rasterize

from tuinscan.config import Config, laag_drempel
from tuinscan.mappen import Mappen
from tuinscan.rasters import _venster, bronnen_uit_cache, grid
from tuinscan.signalen import exg, ndvi_cir, schaduw, vegetatie
from tuinscan.stappen import _lees_cache

KLASSEN = ("rijbaan", "voetpad", "dak", "begroeid")
NIET_GROEN = ("rijbaan", "dak")  # basis voor het voorstel (voetpad: vaak overhangend groen)
KRIMP_M = {"rijbaan": 1.0, "voetpad": 0.3, "dak": 1.0, "begroeid": 1.0}
PERCENTIELEN = (5, 25, 50, 75, 90, 95)
GETOOND = (5, 25, 50, 75, 95)
TEGEL_M = 250.0  # werkgebied in tegels, zodat een grote wijk in het geheugen past
_STAP = 0.001  # histogrambak voor ExG/NDVI in [-1, 1]
_BAKKEN = int(round(2 / _STAP)) + 1
INDEX_LAGEN = {  # laag -> (index, drempelnaam, jaar)
    "zomer_nu": ("ExG", "exg_drempel", "nu"),
    "zomer_vorig": ("ExG", "exg_drempel", "vorig"),
    "cir_nu": ("NDVI", "ndvi_drempel", "nu"),
    "cir_vorig": ("NDVI", "ndvi_drempel", "vorig"),
}


@dataclass
class KlasseStat:
    n: int
    p: dict[int, float]  # percentiel -> waarde
    groen: float | None  # aandeel boven de huidige drempel van deze laag


@dataclass
class LaagStat:
    index_naam: str
    drempel_naam: str
    drempel: float
    klassen: dict[str, KlasseStat]
    voorstel: float | None
    overlap: bool  # niet-groen p90 >= groen p25: geen drempel scheidt de klassen


@dataclass
class Kalibratie:
    lagen: dict[str, LaagStat] = field(default_factory=dict)
    masker: dict[str, dict[str, float | None]] = field(default_factory=dict)  # jaar -> klasse


def _klasse_vlakken(cfg: Config, mappen: Mappen):
    v = cfg.pdok.velden
    weg = _lees_cache(mappen, "bgt_wegdeel")
    groen = _lees_cache(mappen, "bgt_begroeid")
    groen = groen[groen[v["begroeid_fysiek"]].isin(cfg.pdok.openbaar_groen_fysiek)]
    functie = weg[v["wegdeel_functie"]]
    bronnen = {
        "rijbaan": weg[functie.isin(cfg.pdok.rijbaan_functies)].geometry,
        "voetpad": weg[functie.isin(cfg.pdok.voetpad_functies)].geometry,
        "dak": _lees_cache(mappen, "bag_pand").geometry,
        "begroeid": groen.geometry,
    }
    uit = {}
    for k, geoms in bronnen.items():
        klein = geoms.buffer(-KRIMP_M[k])
        uit[k] = klein[~(klein.isna() | klein.is_empty)].reset_index(drop=True)
    return uit


def _masker(geoms, transform, vorm) -> np.ndarray:
    tegel = shapely.box(transform.c, transform.f + transform.e * vorm[0],
                        transform.c + transform.a * vorm[1], transform.f)  # fmt: skip
    idx = geoms.sindex.query(tegel, predicate="intersects") if len(geoms) else []
    if len(idx) == 0:
        return np.zeros(vorm, dtype=bool)
    return rasterize(((g, 1) for g in geoms.iloc[idx]), out_shape=vorm, transform=transform,
                     fill=0, dtype="uint8").astype(bool)  # fmt: skip


def _bak(waarden: np.ndarray) -> np.ndarray:
    i = np.clip(np.rint((waarden + 1.0) / _STAP), 0, _BAKKEN - 1).astype(np.int64)
    return np.bincount(i, minlength=_BAKKEN)


def _percentielen(hist: np.ndarray) -> dict[int, float]:
    n = hist.sum()
    cum = np.cumsum(hist)
    return {q: float(np.searchsorted(cum, q / 100 * n) * _STAP - 1.0) for q in PERCENTIELEN}


def _tegels(bbox):
    x0, y0, x1, y1 = bbox
    for x in np.arange(x0, x1, TEGEL_M):
        for y in np.arange(y0, y1, TEGEL_M):
            yield (x, y, min(x + TEGEL_M, x1), min(y + TEGEL_M, y1))


def meet(cfg: Config, mappen: Mappen) -> tuple[Kalibratie, list[str]]:
    """Meet histogrammen per laag en klasse; geeft (kalibratie, ontbrekende lagen)."""
    bronnen = bronnen_uit_cache(mappen)
    vlakken = _klasse_vlakken(cfg, mappen)
    lagen = [k for k in INDEX_LAGEN if bronnen.get(k) is not None]
    ontbreekt = [k for k in INDEX_LAGEN if k not in lagen]
    hist = {(laag, k): np.zeros(_BAKKEN, dtype=np.int64) for laag in lagen for k in KLASSEN}
    groen = {(jaar, k): [0, 0] for jaar in ("nu", "vorig") for k in KLASSEN}
    res = cfg.signalen.resolutie_m
    for tegel in _tegels(cfg.gebied.bbox_rd):
        transform, vorm = grid(tegel, res)
        maskers = {k: _masker(vlakken[k], transform, vorm) for k in KLASSEN}
        if not any(m.any() for m in maskers.values()):
            continue
        for jaar in ("nu", "vorig"):
            zomer_k, cir_k = f"zomer_{jaar}", f"cir_{jaar}"
            if zomer_k not in lagen:  # zonder RGB-zomerfoto geen schaduwmasker
                continue
            zomer = _venster(bronnen[zomer_k], transform, vorm, Resampling.bilinear)
            cir = (_venster(bronnen[cir_k], transform, vorm, Resampling.bilinear)
                   if cir_k in lagen else None)  # fmt: skip
            licht = ~np.ma.getmaskarray(zomer).any(axis=0)
            licht &= ~schaduw(zomer, cfg.signalen.schaduw_helderheid)
            e = exg(zomer)
            if cir is not None:
                cir_ok = ~np.ma.getmaskarray(cir).any(axis=0)
                nd = ndvi_cir(cir)
            veg = vegetatie(zomer, cir, cfg, jaar)
            for k, m in maskers.items():
                sel = m & licht
                if not sel.any():
                    continue
                hist[(zomer_k, k)] += _bak(e[sel])
                if cir is not None and (sel & cir_ok).any():
                    hist[(cir_k, k)] += _bak(nd[sel & cir_ok])
                groen[(jaar, k)][0] += int((veg & sel).sum())
                groen[(jaar, k)][1] += int(sel.sum())

    uit = Kalibratie()
    for laag in lagen:
        index_naam, naam, _ = INDEX_LAGEN[laag]
        drempel = laag_drempel(cfg, laag, naam)
        klassen = {}
        for k in KLASSEN:
            h = hist[(laag, k)]
            n = int(h.sum())
            if n == 0:
                klassen[k] = KlasseStat(0, {}, None)
                continue
            boven = int(h[int(np.floor((drempel + 1.0) / _STAP + 1e-9)) + 1 :].sum())
            klassen[k] = KlasseStat(n, _percentielen(h), boven / n)
        hard = [klassen[k].p[90] for k in NIET_GROEN if klassen[k].n]
        voorstel, overlap = None, False
        if hard and klassen["begroeid"].n:
            laag_p25 = klassen["begroeid"].p[25]
            voorstel = round((max(hard) + laag_p25) / 2, 2)
            overlap = max(hard) >= laag_p25
        uit.lagen[laag] = LaagStat(index_naam, naam, drempel, klassen, voorstel, overlap)
    for (jaar, k), (teller, noemer) in groen.items():
        if f"zomer_{jaar}" in lagen:
            uit.masker.setdefault(jaar, {})[k] = teller / noemer if noemer else None
    return uit, ontbreekt


def _pct(w: float | None) -> str:
    return "-" if w is None else f"{w:.0%}".replace("%", " %")


def druk_af(cfg: Config, uit: Kalibratie, ontbreekt: list[str]) -> None:
    for laag in ontbreekt:
        print(f"{laag}: ontbreekt in de cache; overgeslagen")
    kop = f"  {'klasse':9s} {'pixels':>8s} " + " ".join(f"{'p' + str(q):>6s}" for q in GETOOND)
    for laag, st in uit.lagen.items():
        naam = (cfg.luchtfoto.lagen.get(laag) or {}).get("laag", "?")
        print(f"\n{laag} ({naam}): {st.index_naam}, huidige {st.drempel_naam} {st.drempel:g}")
        print(kop + "  groen")
        for k, ks in st.klassen.items():
            ps = " ".join(f"{ks.p[q]:6.3f}" if ks.n else f"{'-':>6s}" for q in GETOOND)
            print(f"  {k:9s} {ks.n:8d} {ps}  {_pct(ks.groen)}")
        if st.voorstel is not None:
            print(f"  Voorstel: luchtfoto.lagen.{laag}.{st.drempel_naam}: {st.voorstel:g}")
            if st.overlap:
                print("  Let op: p90 van rijbaan/dak ligt boven p25 van groen; geen drempel "
                      "scheidt ze helemaal (compromis)")  # fmt: skip
    print("\nBegroeiingsmasker met de huidige drempels (NDVI waar CIR geldig is, anders ExG):")
    for jaar, per_klasse in uit.masker.items():
        delen = ", ".join(f"{k} {_pct(w)}" for k, w in per_klasse.items())
        print(f"  {jaar}: {delen}")
    print("Doel: rijbaan en dak hooguit ~10 % groen, begroeid minstens ~85 %. Zet de "
          "voorstellen in de config, draai 'tuinscan kalibreer' opnieuw ter controle en "
          "daarna 'score'.")  # fmt: skip


def kalibreer(cfg: Config, mappen: Mappen) -> Kalibratie:
    uit, ontbreekt = meet(cfg, mappen)
    druk_af(cfg, uit, ontbreekt)
    return uit
