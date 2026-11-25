import os

import pytest

from tuinscan import bestanden
from tuinscan.bestanden import BestandOpen, schrijf_atomair, vervang_samen


def test_schrijf_atomair_replaces_only_on_success(tmp_path):
    doel = tmp_path / "x.csv"
    doel.write_text("oud", encoding="utf-8")
    with pytest.raises(RuntimeError):
        with schrijf_atomair(doel) as tmp:
            tmp.write_text("half", encoding="utf-8")
            raise RuntimeError("stuk")
    assert doel.read_text(encoding="utf-8") == "oud"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.csv"]
    with schrijf_atomair(doel) as tmp:
        assert tmp.suffix == ".csv"  # GDAL/pandas herkennen het formaat aan de extensie
        tmp.write_text("nieuw", encoding="utf-8")
    assert doel.read_text(encoding="utf-8") == "nieuw"


def test_locked_target_raises_bestand_open_and_cleans_up(tmp_path, monkeypatch):
    eerst, daarna = tmp_path / "tuinen.gpkg", tmp_path / "vhe.parquet"
    daarna.write_text("oud", encoding="utf-8")
    echt = os.replace

    def slot(a, b):
        if str(b).endswith("tuinen.gpkg"):
            raise PermissionError(13, "in gebruik", str(b))
        return echt(a, b)

    monkeypatch.setattr(bestanden.os, "replace", slot)
    with pytest.raises(BestandOpen) as e:
        with vervang_samen(eerst, daarna) as (t1, t2):
            t1.write_text("g", encoding="utf-8")
            t2.write_text("nieuw", encoding="utf-8")
    assert e.value.filename == str(eerst) and isinstance(e.value, PermissionError)
    assert daarna.read_text(encoding="utf-8") == "oud"  # niets half vervangen
    assert sorted(p.name for p in tmp_path.iterdir()) == ["vhe.parquet"]
