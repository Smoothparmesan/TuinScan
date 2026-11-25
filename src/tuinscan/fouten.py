"""Verzamelt per-VHE fouten; een falend adres stopt nooit een stap (N06)."""

import csv
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from tuinscan.bestanden import schrijf_atomair

KOLOMMEN = ["vhe_nummer", "stap", "reden"]
# Nederlandse Excel: puntkomma als scheidingsteken en een BOM, anders worden kolommen en
# tekens als 'm²' verkeerd gelezen.
CSV_SEP = ";"
CSV_CODERING = "utf-8-sig"


@dataclass
class Foutenlijst:
    regels: list[tuple[str, str, str]] = field(default_factory=list)

    def voeg_toe(self, vhe_nummer: str, stap: str, reden: str) -> None:
        self.regels.append((str(vhe_nummer), stap, reden))

    @property
    def aantal(self) -> int:
        return len(self.regels)

    def vhe_nummers(self) -> set[str]:
        """Alle VHE-nummers met een regel. Geen uitsluitlijst: bij een dubbel VHE-nummer
        staat de eerste rij gewoon in de resultaten."""
        return {r[0] for r in self.regels}

    def schrijf(self, pad: Path) -> None:
        with schrijf_atomair(pad) as tmp:
            with tmp.open("w", encoding=CSV_CODERING, newline="") as f:
                w = csv.writer(f, delimiter=CSV_SEP)
                w.writerow(KOLOMMEN)
                w.writerows(self.regels)


def lees_foutenlijst(pad: Path) -> pd.DataFrame:
    """Lees foutenlijst.csv (ook een oudere met komma's); ontbreekt hij, dan een lege tabel."""
    pad = Path(pad)
    if pad.exists():
        for sep in (CSV_SEP, ","):
            df = pd.read_csv(pad, sep=sep, dtype=str, keep_default_na=False,
                             encoding=CSV_CODERING)  # fmt: skip
            if list(df.columns) == KOLOMMEN:
                return df
    return pd.DataFrame(columns=KOLOMMEN)
