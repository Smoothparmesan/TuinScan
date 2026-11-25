"""Terugkoppelbestand (Excel) voor bezoeken, inlezen en precisie per signaal."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from tuinscan.bestanden import schrijf_atomair
from tuinscan.config import Config
from tuinscan.fouten import CSV_CODERING, CSV_SEP

BLAD = "Terugkoppeling"
LIJSTEN = "Lijsten"
HEADERS = [
    "Groep", "Rang", "VHE", "Adres", "Wijk", "Score", "Reden 1", "Reden 2",
    "Redencodes", "Bezoekdatum", "Oordeel", "Oorzaak", "Actie",
]  # fmt: skip
BREEDTES = [9, 6, 12, 28, 16, 8, 36, 36, 14, 13, 12, 28, 16]
RIJEN = 1000
CRITERIUM = 0.5


def _vhe_sleutel(v: object) -> tuple[int, int, str]:
    s = str(v)
    return (0, int(s), s) if s.isdigit() else (1, 0, s)


def selecteer_bezoeklijst(
    scores: pd.DataFrame, top_n: int, controle_n: int, seed: int
) -> pd.DataFrame:
    """Top `top_n` per wijk plus een vaste steekproef uit de rest als controlegroep."""
    s = scores.copy()
    s["vhe_nummer"] = s["vhe_nummer"].astype(str)
    is_top = s["rang_in_wijk"] <= top_n
    top = s[is_top].assign(groep="top")
    top = top.sort_values(["rang_in_wijk", "wijk", "vhe_nummer"], key=_sleutel_kolom)
    rest = s[~is_top]
    k = min(controle_n, len(rest))
    if k > 0:
        pick = np.random.default_rng(seed).choice(len(rest), size=k, replace=False)
        ctrl = rest.iloc[np.sort(pick)].assign(groep="controle")
        ctrl = ctrl.sort_values("vhe_nummer", key=_sleutel_kolom)
    else:
        ctrl = rest.iloc[0:0].assign(groep="controle")
    return pd.concat([top, ctrl], ignore_index=True)


def _sleutel_kolom(kol: pd.Series) -> pd.Series:
    if kol.name == "vhe_nummer":
        return kol.map(_vhe_sleutel)
    return kol


def _tekst(w: object) -> str:
    if w is None or (isinstance(w, float) and math.isnan(w)):
        return ""
    if isinstance(w, (list, tuple, np.ndarray)):
        return ",".join(str(x) for x in w)
    return str(w)


def maak_terugkoppelbestand(lijst: pd.DataFrame, cfg: Config, pad: Path) -> Path:
    t = cfg.terugkoppeling
    rijen_max = max(RIJEN, len(lijst) + 1)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = BLAD
    ws.append(HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in lijst.itertuples():
        ws.append([
            r.groep, int(r.rang_in_wijk), str(r.vhe_nummer), _tekst(r.adres), _tekst(r.wijk),
            round(float(r.score), 1), _tekst(r.reden_1), _tekst(r.reden_2),
            _tekst(r.reden_codes), None, None, None, None,
        ])  # fmt: skip
    for rij in range(2, rijen_max + 2):
        ws.cell(row=rij, column=3).number_format = "@"
        ws.cell(row=rij, column=10).number_format = "dd-mm-yyyy"
    for i, b in enumerate(BREEDTES, start=1):
        ws.column_dimensions[get_column_letter(i)].width = b
    ws.freeze_panes = "A2"

    lw = wb.create_sheet(LIJSTEN)
    lijsten = {"Oordeel": t.oordeel, "Oorzaak": t.oorzaak, "Actie": t.actie}
    for kol, (naam, waarden) in enumerate(lijsten.items(), start=1):
        lw.cell(row=1, column=kol, value=naam)
        for i, w in enumerate(waarden, start=2):
            lw.cell(row=i, column=kol, value=w)
        letter = get_column_letter(kol)
        dv = DataValidation(
            type="list",
            formula1=f"={LIJSTEN}!${letter}$2:${letter}${len(waarden) + 1}",
            allow_blank=True,
            showErrorMessage=True,
            errorTitle=f"Ongeldige {naam}",
            error=f"Kies een waarde uit de lijst voor {naam}.",
        )
        dv.add(f"{get_column_letter(HEADERS.index(naam) + 1)}2:"
               f"{get_column_letter(HEADERS.index(naam) + 1)}{rijen_max + 1}")  # fmt: skip
        ws.add_data_validation(dv)
    dd = DataValidation(
        type="date", operator="greaterThan", formula1="36526", allow_blank=True,
        showErrorMessage=True, errorTitle="Ongeldige datum", error="Vul een datum in.",
    )  # fmt: skip
    dd.add(f"J2:J{rijen_max + 1}")
    ws.add_data_validation(dd)
    lw.cell(row=1, column=4, value="VHE")
    lw.cell(row=1, column=5, value="Groep")
    for i, r in enumerate(lijst.itertuples(), start=2):
        c = lw.cell(row=i, column=4, value=str(r.vhe_nummer))
        c.number_format = "@"
        lw.cell(row=i, column=5, value=r.groep)
    lw.sheet_state = "hidden"
    pad = Path(pad)
    with schrijf_atomair(pad) as tmp:
        wb.save(tmp)
    return pad


def heeft_oordelen(pad: Path) -> bool:
    """Staat er in het bestaande bestand al een Oordeel, Oorzaak, Actie of Bezoekdatum?"""
    try:
        wb = openpyxl.load_workbook(pad, read_only=True)
        try:
            kols = [HEADERS.index(h) for h in ("Bezoekdatum", "Oordeel", "Oorzaak", "Actie")]
            return any(
                any(len(r) > k and r[k] is not None and str(r[k]).strip() != "" for k in kols)
                for r in wb[BLAD].iter_rows(min_row=2, values_only=True)
            )
        finally:
            wb.close()
    except Exception:
        return True  # onleesbaar: liever niet overschrijven


def _norm(w: object) -> str:
    return " ".join(str(w).split()).casefold()


def _vhe_tekst(w: object) -> str:
    if isinstance(w, float) and w.is_integer():
        return str(int(w))
    return str(w).strip()


def _bekende_vhes(wb, extra) -> dict[str, str]:
    """VHE -> groep uit de verborgen lijst in het bestand, aangevuld met `extra` (VHE's)."""
    bekend: dict[str, str] = {}
    if LIJSTEN in wb.sheetnames:
        for r in wb[LIJSTEN].iter_rows(min_row=2, min_col=4, max_col=5, values_only=True):
            if r[0] is not None and str(r[0]).strip():
                bekend[str(r[0]).strip()] = _tekst(r[1]) or "top"
    for v in extra or ():
        bekend.setdefault(str(v), "")
    return bekend


def lees_terugkoppeling(
    pad: Path, cfg: Config, bekend_extra=None
) -> tuple[pd.DataFrame, list[str]]:
    """Lees het ingevulde bestand; sleutel is de VHE, ongeldige rijen komen in de meldingen."""
    t = cfg.terugkoppeling
    wb = openpyxl.load_workbook(pad, data_only=True)
    ws = wb[BLAD]
    koppen = [_norm(c.value) for c in ws[1]]
    idx = {h: koppen.index(_norm(h)) for h in HEADERS if _norm(h) in koppen}
    geldig = {
        "Oordeel": {_norm(w): w for w in t.oordeel},
        "Oorzaak": {_norm(w): w for w in t.oorzaak},
        "Actie": {_norm(w): w for w in t.actie},
    }

    def cel(rij, h):
        i = idx.get(h)
        w = rij[i] if i is not None and i < len(rij) else None
        return None if w is None or str(w).strip() == "" else w

    bekend = _bekende_vhes(wb, bekend_extra)
    zonder_nul = {int(k): k for k in bekend if k.isdigit()}
    rijen, meldingen = {}, []
    for n, rij in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        vhe, oordeel = cel(rij, "VHE"), cel(rij, "Oordeel")
        if not any(cel(rij, h) for h in ("Oordeel", "Oorzaak", "Actie")):
            continue
        if vhe is None:
            meldingen.append(f"rij {n}: VHE ontbreekt")
            continue
        if oordeel is None:
            meldingen.append(f"rij {n}: Oordeel ontbreekt")
            continue
        v = _vhe_tekst(vhe)
        if bekend and v not in bekend:
            v_int = int(v) if v.isdigit() else None
            if v_int in zonder_nul:
                v = zonder_nul[v_int]
            else:
                meldingen.append(f"rij {n}: VHE '{v}' niet in de lijst")
                continue
        uit, fout = {}, False
        for veld in ("Oordeel", "Oorzaak", "Actie"):
            w = cel(rij, veld)
            if w is None:
                uit[veld] = None
            elif _norm(w) in geldig[veld]:
                uit[veld] = geldig[veld][_norm(w)]
            else:
                meldingen.append(f"rij {n}: '{w}' is geen geldige {veld}")
                fout = True
        if fout:
            continue
        if v in rijen:
            meldingen.append(f"rij {n}: dubbele VHE, laatste telt")
        rijen[v] = {
            "vhe_nummer": v, "groep": _tekst(cel(rij, "Groep")) or bekend.get(v) or "top",
            "bezoekdatum": cel(rij, "Bezoekdatum"), "oordeel": uit["Oordeel"],
            "oorzaak": uit["Oorzaak"], "actie": uit["Actie"],
        }  # fmt: skip
    kol = ["vhe_nummer", "groep", "bezoekdatum", "oordeel", "oorzaak", "actie"]
    return pd.DataFrame(list(rijen.values()), columns=kol), meldingen


def kwaliteit(scores: pd.DataFrame, terug: pd.DataFrame) -> pd.DataFrame:
    """Precisie per signaalcode plus aandeel terecht in top- en controlegroep."""
    t = terug.copy()
    t["vhe_nummer"] = t["vhe_nummer"].astype(str)
    if "groep" not in t.columns:
        t["groep"] = "top"
    t = t.drop_duplicates("vhe_nummer", keep="last")
    codes = scores.assign(vhe_nummer=scores["vhe_nummer"].astype(str)).set_index("vhe_nummer")[
        "reden_codes"
    ]
    oordeel = t.set_index("vhe_nummer")["oordeel"].map(_norm)

    def rij(naam: str, ids) -> dict:
        o = oordeel[oordeel.index.isin(ids)]
        n, k = len(o), int((o == "terecht").sum())
        return {"onderdeel": naam, "n_beoordeeld": n, "n_terecht": k,
                "precisie": k / n if n else float("nan")}  # fmt: skip

    alle = sorted({str(c) for lijst in codes for c in (lijst if lijst is not None else [])})
    rijen = [rij(c, [v for v, cs in codes.items() if cs is not None and c in list(cs)])
             for c in alle]  # fmt: skip
    for naam, groep in (("top_n", "top"), ("controle", "controle")):
        rijen.append(rij(naam, set(t.loc[t["groep"] == groep, "vhe_nummer"])))
    return pd.DataFrame(rijen, columns=["onderdeel", "n_beoordeeld", "n_terecht", "precisie"])


def schrijf_kwaliteit(kw: pd.DataFrame, top_n: int, pad: Path) -> None:
    """Schrijf kwaliteit.csv (NaN leeg), druk de tabel en het succescriterium af."""
    with schrijf_atomair(Path(pad)) as tmp:  # Nederlandse Excel: ';' en decimale komma
        kw.to_csv(tmp, index=False, na_rep="", sep=CSV_SEP, decimal=",", encoding=CSV_CODERING)
    print(kw.to_string(index=False, na_rep="-", float_format=lambda x: f"{x:.2f}"))
    r = kw[kw["onderdeel"] == "top_n"].iloc[0]
    m, k = int(r["n_beoordeeld"]), int(r["n_terecht"])
    gehaald = m > 0 and k / m >= CRITERIUM
    print(
        f"Top-{top_n} per wijk: {k} van {m} terecht - succescriterium >= 50 %: "
        f"{'GEHAALD' if gehaald else 'NIET GEHAALD'}"
    )
