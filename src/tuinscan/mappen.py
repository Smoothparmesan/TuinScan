"""Maplayout en OneDrive-guard: databestanden mogen niet in een sync-map staan."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tuinscan.config import Config, ConfigFout

ONEDRIVE_ENV = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")
ONGELDIGE_TEKENS = '/\\:*?"<>|'
GERESERVEERD = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}  # fmt: skip


def _ongeldige_ronde(naam: str) -> bool:
    """Geen padtekens, geen Windows-verboden tekens of namen (ook 'con.txt'), geen punt of
    spatie aan het eind (Windows haalt die stilzwijgend weg)."""
    return (
        not naam
        or naam in (".", "..")
        or ".." in naam
        or any(c in naam for c in ONGELDIGE_TEKENS)
        or any(ord(c) < 32 for c in naam)
        or naam[-1] in ". "
        or naam.split(".")[0].strip().upper() in GERESERVEERD
    )


class OneDriveFout(Exception):
    """Een databoom staat in een OneDrive-map."""


@dataclass(frozen=True)
class Mappen:
    invoer: Path
    cache: Path
    uitvoer: Path
    ronde_dir: Path

    @classmethod
    def uit_config(cls, cfg: Config, ronde: str | None) -> "Mappen":
        naam = ronde or cfg.gebied.naam
        if _ongeldige_ronde(naam):
            raise ConfigFout(
                f"ronde: ongeldige naam {naam!r} (geen padtekens, '..', {ONGELDIGE_TEKENS[3:]}, "
                "punt of spatie aan het eind, of Windows-namen als CON, NUL, COM1)"
            )
        uitvoer = Path(cfg.mappen.uitvoer)
        return cls(
            invoer=Path(cfg.mappen.invoer),
            cache=Path(cfg.mappen.cache),
            uitvoer=uitvoer,
            ronde_dir=uitvoer / naam,
        )

    def alle(self) -> list[Path]:
        return [self.invoer, self.cache, self.uitvoer]


def _norm(p: Path | str) -> str:
    return os.path.normcase(os.path.abspath(str(p))).rstrip("\\/")


def controleer_buiten_onedrive(paden: list[Path], env: Mapping[str, str]) -> None:
    wortels = [_norm(env[k]) for k in ONEDRIVE_ENV if env.get(k)]
    for pad in paden:
        n = _norm(pad)
        for w in wortels:
            if n == w or n.startswith(w + os.sep) or n.startswith(w + "/"):
                raise OneDriveFout(
                    f"{pad} staat in OneDrive ({w}); kies een map buiten OneDrive-sync "
                    "in 'mappen' van de config"
                )
        if any(deel.lower().startswith("onedrive") for deel in Path(pad).parts):
            raise OneDriveFout(
                f"{pad} bevat een OneDrive-map; kies een map buiten OneDrive-sync "
                "in 'mappen' van de config"
            )
