"""Vaste 0-100-score uit de signalen, met redenen en aggregatie over tuindelen.

Gewichten, grenzen en drempels komen uit `cfg.score` en `cfg.signalen`; de schaal is
vast (niet per wijk), zodat scores tussen wijken en rondes vergelijkbaar blijven.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tuinscan.config import Config

REDEN_TEKST = {
    "S1": "Groentoename: +{v:.0%} groen t.o.v. vorige zomer",
    "S2": "Hoge begroeiing: {v:.0%} van de tuin hoger dan {h} m",
    "S3": "Overhang: {v:.1f} m² begroeiing boven stoep of openbaar groen",
    "S4a": "Hoog object: {v:.1f} m² niet in BAG",
    "S5": "Verharding: {v:.0%} blijvend niet-groen",
}
OPPERVLAK_SIGNALEN = ("S3", "S4a")  # som over delen
AANDEEL_SIGNALEN = ("S1", "S2", "S5", "S5_toename")  # oppervlakgewogen gemiddelde
MAX_REDENEN = 2


def schaal(x: float, onder: float, boven: float) -> float:
    return min(1.0, max(0.0, (x - onder) / (boven - onder)))


@dataclass(frozen=True)
class ScoreUitkomst:
    score: float
    bijdragen: dict[str, float]
    redenen: list[str]
    reden_codes: list[str]
    ontbrekend: list[str]


def bereken_score(signalen: dict[str, float | None], cfg: Config) -> ScoreUitkomst:
    bijdragen: dict[str, float] = {}
    ontbrekend: list[str] = []
    for naam, regel in cfg.score.items():
        x = signalen.get(naam)
        if pd.isna(x):
            ontbrekend.append(naam)
            bijdragen[naam] = 0.0
        else:
            bijdragen[naam] = regel.gewicht * schaal(x, regel.onder, regel.boven)
    zwaarst = sorted(
        (n for n, b in bijdragen.items() if b > 0 and n in REDEN_TEKST),
        key=lambda n: -bijdragen[n],
    )[:MAX_REDENEN]
    redenen = [
        REDEN_TEKST[n].format(v=signalen[n], h=f"{cfg.signalen.s2_hoogte_m:g}") for n in zwaarst
    ]
    return ScoreUitkomst(
        score=round(sum(bijdragen.values()), 1),
        bijdragen=bijdragen,
        redenen=redenen,
        reden_codes=zwaarst,
        ontbrekend=ontbrekend,
    )


def aggregeer_delen(delen: list[tuple[float, dict[str, float | None]]]) -> dict[str, float | None]:
    """Combineer (oppervlak_m2, signalen) per tuindeel tot één waarde per VHE.

    Oppervlaksignalen: som over delen met een waarde; aandelen: oppervlakgewogen gemiddelde
    over delen met een waarde. Geen enkel deel met waarde -> None.
    """
    uit: dict[str, float | None] = {}
    sleutels = {k for _, s in delen for k in s}
    for k in sleutels:
        met = [(m2, s[k]) for m2, s in delen if not pd.isna(s.get(k))]
        if not met:
            uit[k] = None
        elif k in OPPERVLAK_SIGNALEN:
            uit[k] = sum(v for _, v in met)
        else:
            totaal = sum(m2 for m2, _ in met)
            uit[k] = (
                sum(m2 * v for m2, v in met) / totaal
                if totaal > 0
                else sum(v for _, v in met) / len(met)
            )
    return uit
