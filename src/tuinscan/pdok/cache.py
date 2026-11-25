"""Cache-manifest en cachepaden."""

from __future__ import annotations

import json
import os
from pathlib import Path


def vector_pad(cache: Path, sleutel: str) -> Path:
    return cache / "vector" / f"{sleutel}.gpkg"


def _norm(bbox) -> list[float]:
    return [round(float(v), 3) for v in bbox]


class Manifest:
    def __init__(self, pad: Path):
        self.pad = Path(pad)

    def _laad(self) -> dict:
        if not self.pad.exists():
            return {}
        return json.loads(self.pad.read_text(encoding="utf-8"))

    def heeft(self, sleutel: str, bbox) -> bool:
        rec = self._laad().get(sleutel)
        return rec is not None and _norm(rec["bbox"]) == _norm(bbox)

    def registreer(self, sleutel: str, url: str, bbox, extra: dict) -> None:
        data = self._laad()
        data[sleutel] = {**extra, "url": url, "bbox": _norm(bbox)}
        self.pad.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.pad.with_name(self.pad.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.pad)

    def lees(self, sleutel: str) -> dict:
        return self._laad()[sleutel]
