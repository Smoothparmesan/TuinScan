"""Uitvoer veilig schrijven: eerst een tijdelijk bestand, dan `os.replace`.

Op Windows mislukt `os.replace` als het doel open staat (QGIS, Excel). Dan blijft het oude
bestand heel, verdwijnen de tijdelijke bestanden en volgt `BestandOpen`, zodat de CLI kan
zeggen welk bestand gesloten moet worden. `vervang_samen` vervangt meerdere bestanden pas
als ze allemaal geschreven zijn, in de opgegeven volgorde (zet het bestand dat het vaakst
open staat, zoals tuinen.gpkg, vooraan).
"""

from __future__ import annotations

import errno
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class BestandOpen(PermissionError):
    """Een uitvoerbestand kan niet worden vervangen; waarschijnlijk staat het open."""

    def __init__(self, pad: Path):
        super().__init__(errno.EACCES, "bestand is in gebruik", str(pad))
        self.pad = Path(pad)


def tijdelijk_pad(doel: Path) -> Path:
    """Tijdelijke naam naast `doel`, met dezelfde extensie (GDAL/pandas kijken daarnaar)."""
    doel = Path(doel)
    return doel.with_name(f"{doel.stem}.tmp{doel.suffix}")


def _weg(paden) -> None:
    for p in paden:
        for extra in ("", "-journal", "-wal", "-shm"):  # GPKG/SQLite-hulpbestanden
            Path(f"{p}{extra}").unlink(missing_ok=True)


@contextmanager
def vervang_samen(*doelen: Path) -> Iterator[list[Path]]:
    """Geef tijdelijke paden; vervang na een geslaagd blok alle doelen (in volgorde)."""
    doelen = [Path(d) for d in doelen]
    tmps = [tijdelijk_pad(d) for d in doelen]
    for d in doelen:
        d.parent.mkdir(parents=True, exist_ok=True)
    _weg(tmps)
    try:
        yield tmps
    except BaseException:
        _weg(tmps)
        raise
    for i, (tmp, doel) in enumerate(zip(tmps, doelen, strict=True)):
        try:
            os.replace(tmp, doel)
        except PermissionError as e:
            _weg(tmps[i:])
            raise BestandOpen(doel) from e


@contextmanager
def schrijf_atomair(doel: Path) -> Iterator[Path]:
    """Eén bestand: schrijf naar het gegeven tijdelijke pad; vervang `doel` bij succes."""
    with vervang_samen(doel) as (tmp,):
        yield tmp
