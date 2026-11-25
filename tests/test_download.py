from pathlib import Path

import geopandas as gpd
import pytest
import yaml
from shapely.geometry import Point, box

import tuinscan.download as dl
from tuinscan.config import laad_config
from tuinscan.download import lees_vector, voer_download_uit
from tuinscan.mappen import Mappen
from tuinscan.pdok.wmts import Capabilities, MozaiekInfo, TileMatrix

VOORBEELD = Path(__file__).parent.parent / "config" / "tuinscan.example.yaml"


def maak_cfg(tmp_path, wijzig=None):
    data = yaml.safe_load(VOORBEELD.read_text(encoding="utf-8"))
    data["mappen"] = {k: str(tmp_path / k) for k in ("invoer", "cache", "uitvoer")}
    data["gebied"]["bbox_rd"] = [100.0, 200.0, 300.0, 400.0]
    data["gebied"]["buffer_m"] = 10
    if wijzig:
        wijzig(data)
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    cfg = laad_config(p)
    return cfg, Mappen.uit_config(cfg, None)


@pytest.fixture
def aanroepen(monkeypatch):
    log = {"vector": [], "mozaiek": [], "ahn": []}
    mislukt = {"n": 0}

    def features(url, coll, bbox, sessie, limit=1000):
        log["vector"].append((coll, tuple(bbox)))
        return gpd.GeoDataFrame(
            {"feature_id": ["a"], "pand.href": [["x/1", "x/2"]]},
            geometry=[Point(1, 2)], crs=28992,
        )

    def mozaiek(url, laag, matrixset, matrix, formaat, bbox, doel, sessie, workers):
        log["mozaiek"].append((laag, tuple(bbox)))
        doel.parent.mkdir(parents=True, exist_ok=True)
        doel.write_bytes(b"x")
        return MozaiekInfo(doel, 4, mislukt["n"] or 0)

    def ahn(bronnen, bbox, doel):
        log["ahn"].append((tuple(bronnen), tuple(bbox)))
        doel.parent.mkdir(parents=True, exist_ok=True)
        doel.write_bytes(b"x")
        return doel

    m = TileMatrix("14", 0.21, 0, 0, 256, 256, 10, 10)
    caps = Capabilities(
        {f"{j}_{t}": "image/jpeg" for j in (2024, 2025, 2026)
         for t in ("ortho25", "ortho25IR", "orthoHR")},
        {"14": m, "15": m},
    )
    index = gpd.GeoDataFrame({"url": ["k.tif"]}, geometry=[box(0, 0, 1000, 1000)], crs=28992)
    monkeypatch.setattr(dl, "haal_features", features)
    monkeypatch.setattr(dl, "download_mozaiek", mozaiek)
    monkeypatch.setattr(dl, "lees_ahn", ahn)
    monkeypatch.setattr(dl, "haal_capabilities", lambda *a, **k: caps)
    monkeypatch.setattr(dl, "_laad_index", lambda *a, **k: index)
    log["mislukt"] = mislukt
    return log


def totaal(log):
    return len(log["vector"]) + len(log["mozaiek"]) + len(log["ahn"])


def test_download_skips_cached(tmp_path, aanroepen):
    cfg, mp = maak_cfg(tmp_path)
    r1 = voer_download_uit(cfg, mp, False)
    assert len(r1.opgehaald) == 5 + 6 + 2
    n = totaal(aanroepen)
    r2 = voer_download_uit(cfg, mp, False)
    assert totaal(aanroepen) == n and r2.opgehaald == [] and len(r2.overgeslagen) == 13


def test_download_vernieuw_refetches(tmp_path, aanroepen):
    cfg, mp = maak_cfg(tmp_path)
    voer_download_uit(cfg, mp, False)
    n = totaal(aanroepen)
    r = voer_download_uit(cfg, mp, True)
    assert totaal(aanroepen) == 2 * n and r.overgeslagen == []


def test_unconfigured_layer_skipped(tmp_path, aanroepen):
    def w(d):
        d["luchtfoto"]["lagen"]["zomer_vorig"]["laag"] = None

    cfg, mp = maak_cfg(tmp_path, w)
    r = voer_download_uit(cfg, mp, False)
    assert r.niet_geconfigureerd == ["zomer_vorig"]
    assert all(laag != "2024_ortho25" for laag, _ in aanroepen["mozaiek"])


def test_bbox_is_only_spatial_input(tmp_path, aanroepen):
    cfg, mp = maak_cfg(tmp_path)
    voer_download_uit(cfg, mp, False)
    grown = (90.0, 190.0, 310.0, 410.0)
    boxes = [b for _, b in aanroepen["vector"] + aanroepen["mozaiek"] + aanroepen["ahn"]]
    assert len(boxes) == 13 and set(boxes) == {grown}


def test_empty_mosaic_registered_as_leeg(tmp_path, aanroepen, caplog):
    aanroepen["mislukt"]["n"] = 4
    cfg, mp = maak_cfg(tmp_path)
    with caplog.at_level("WARNING", logger="tuinscan"):
        r = voer_download_uit(cfg, mp, False)
    assert len(r.leeg) == 6 and "ontbreken" in caplog.text
    from tuinscan.pdok.cache import Manifest

    assert Manifest(mp.cache / "manifest.json").lees("zomer_nu")["status"] == "leeg"


def test_vector_roundtrip_list_columns(tmp_path, aanroepen):
    cfg, mp = maak_cfg(tmp_path)
    voer_download_uit(cfg, mp, False)
    gdf = lees_vector(mp.cache / "vector" / "bag_vbo.gpkg")
    assert gdf["pand.href"].iloc[0] == ["x/1", "x/2"]
    assert gdf["feature_id"].iloc[0] == "a"
