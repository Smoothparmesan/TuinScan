"""`tuinscan inspecteer`: toon exportkolommen met voorgestelde koppeling."""

import sys

import yaml

from tuinscan.config import Config, ConfigFout
from tuinscan.mappen import Mappen
from tuinscan.tobias import lees_ruwe_export, stel_mapping_voor

_MAX = 30


def _kort(s: str) -> str:
    return s if len(s) <= _MAX else s[: _MAX - 1] + "…"


def inspecteer(cfg: Config, mappen: Mappen) -> int:
    t = cfg.tobias
    try:
        df = lees_ruwe_export(mappen.invoer / t.bestand, t.blad, t.kopregel)
    except ConfigFout as e:
        print(f"Fout: {e}", file=sys.stderr)
        return 1
    voorstel = stel_mapping_voor(df)
    per_kolom = {v.kolom: (veld, v) for veld, v in voorstel.items() if v.kolom}
    for kol in df.columns:
        monsters = [_kort(str(w)) for w in df[kol].dropna().head(3)]
        veld, v = per_kolom.get(kol, ("-", None))
        zek = v.zekerheid if v else "-"
        print(f"{kol} | {' ; '.join(monsters)} | {veld} | {zek}")
    mapping = {veld: v.kolom for veld, v in voorstel.items() if v.kolom}
    doel = mappen.invoer / t.kolommen_bestand
    if doel.exists():
        doel = doel.with_name(doel.stem + ".voorstel" + doel.suffix)
    doel.write_text(yaml.safe_dump(mapping, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"Voorstel geschreven naar {doel}; controleer en vul ontbrekende velden aan.")
    return 0
