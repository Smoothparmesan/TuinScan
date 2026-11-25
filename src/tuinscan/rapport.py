"""Offline HTML-rapport: één zelfstandig bestand met ingebedde thumbnails."""

from __future__ import annotations

import base64
from collections import Counter
from pathlib import Path

import pandas as pd
from jinja2 import Environment, PackageLoader, select_autoescape

BANNER = "Signaallijst – de wijkbeheerder beslist altijd zelf ter plekke. Niet mailen of delen."
DETAILS = [
    ("detail_winter_nu", "Winter, dit jaar"),
    ("detail_winter_vorig", "Winter, vorig jaar"),
    ("detail_zomer_nu", "Zomer, dit jaar"),
    ("detail_zomer_vorig", "Zomer, vorig jaar"),
]


def _leeg(w) -> bool:
    return w is None or (not hasattr(w, "__len__") and bool(pd.isna(w)))


def data_uri(pad: Path | None) -> str | None:
    if pad is None or not Path(pad).exists():
        return None
    b64 = base64.b64encode(Path(pad).read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _tekst(w) -> str | None:
    return None if _leeg(w) else str(w)


def bouw_rijen(scores: pd.DataFrame, uitsneden: dict[str, dict[str, Path | None]]) -> list[dict]:
    rijen = []
    for i, r in enumerate(scores.to_dict("records")):
        vhe = str(r["vhe_nummer"])
        pad = uitsneden.get(vhe, {})
        rijen.append({
            "id": f"v{i}", "vhe": vhe, "wijk": _tekst(r["wijk"]) or "(onbekend)",
            "rang": int(r["rang_in_wijk"]), "adres": _tekst(r["adres"]) or "",
            "complex": _tekst(r["complex"]) or "", "score": float(r["score"]),
            "reden_1": _tekst(r.get("reden_1")), "reden_2": _tekst(r.get("reden_2")),
            "ontbrekend": [str(s) for s in ([] if _leeg(r.get("ontbrekend")) else r["ontbrekend"])],
            "thumb_nu": data_uri(pad.get("thumb_zomer_nu")),
            "thumb_vorig": data_uri(pad.get("thumb_zomer_vorig")),
            "details": [
                (f"uitsneden/{Path(pad[k]).name}", titel)
                for k, titel in DETAILS if pad.get(k) is not None
            ],
        })  # fmt: skip
    return rijen


def render_rapport(
    scores: pd.DataFrame,
    uitsneden: dict[str, dict[str, Path | None]],
    *,
    ronde: str,
    n_tuinen: int,
    fouten: pd.DataFrame,
    kaartbladen: list[str] | None = None,
) -> str:
    rijen = bouw_rijen(scores, uitsneden)
    wijken: dict[str, list[dict]] = {}
    for r in sorted(rijen, key=lambda r: (r["wijk"], -r["score"], r["rang"])):
        wijken.setdefault(r["wijk"], []).append(r)
    herkomst = None
    if len(scores):
        e = scores.iloc[0]
        sleutels = ("zomer_jaar", "zomer_vorig_jaar", "winter_jaar", "ahn_versie", "parameterset")
        herkomst = {k: _tekst(e.get(k)) or "–" for k in sleutels}
        herkomst = {k: (v[:-2] if v.endswith(".0") else v) for k, v in herkomst.items()}
    per_stap = sorted(Counter(fouten["stap"]).items()) if len(fouten) else []
    env = Environment(
        loader=PackageLoader("tuinscan", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
    )
    return env.get_template("rapport.html.j2").render(
        banner=BANNER, ronde=ronde, herkomst=herkomst, wijken=wijken, n_vhe=len(scores),
        n_tuinen=n_tuinen, n_fouten=len(fouten), per_stap=per_stap,
        kaartbladen=list(kaartbladen or []),
    )  # fmt: skip
