"""Verzamelt per-VHE fouten; een falend adres stopt nooit een stap (N06)."""

import csv
from dataclasses import dataclass, field
from pathlib import Path

KOLOMMEN = ["vhe_nummer", "stap", "reden"]


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
        pad.parent.mkdir(parents=True, exist_ok=True)
        with pad.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(KOLOMMEN)
            w.writerows(self.regels)
