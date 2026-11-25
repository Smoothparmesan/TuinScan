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


def test_ahn_outside_coverage_does_not_crash(tmp_path, aanroepen, monkeypatch):
    far = gpd.GeoDataFrame({"url": ["k.tif"]}, geometry=[box(9e5, 9e5, 9.1e5, 9.1e5)], crs=28992)
    monkeypatch.setattr(dl, "_laad_index", lambda *a, **k: far)
    cfg, mp = maak_cfg(tmp_path)
    r = voer_download_uit(cfg, mp, False)
    assert set(r.mislukt) == {"ahn_dtm", "ahn_dsm"} and len(r.opgehaald) == 11


def test_ahn_read_error_recorded(tmp_path, aanroepen, monkeypatch):
    from rasterio.errors import RasterioIOError

    def stuk(*a, **k):
        raise RasterioIOError("kapot")

    monkeypatch.setattr(dl, "lees_ahn", stuk)
    cfg, mp = maak_cfg(tmp_path)
    assert set(voer_download_uit(cfg, mp, False).mislukt) == {"ahn_dtm", "ahn_dsm"}


def test_missing_wmts_layer_or_failure_continues(tmp_path, aanroepen, monkeypatch):
    def w(d):
        d["luchtfoto"]["lagen"]["winter_nu"]["laag"] = "2099_orthoHR"
        d["luchtfoto"]["lagen"]["winter_vorig"]["zoom"] = "99"

    orig = dl.download_mozaiek

    def stuk(url, laag, *a, **k):
        if laag == "2024_ortho25":
            raise OSError("netwerk")
        return orig(url, laag, *a, **k)

    monkeypatch.setattr(dl, "download_mozaiek", stuk)
    cfg, mp = maak_cfg(tmp_path, w)
    r = voer_download_uit(cfg, mp, False)
    assert set(r.mislukt) == {"winter_nu", "winter_vorig", "zomer_vorig"}
    assert "cir_nu" in r.opgehaald


def test_leeg_mosaic_is_refetched(tmp_path, aanroepen):
    aanroepen["mislukt"]["n"] = 4
    cfg, mp = maak_cfg(tmp_path)
    voer_download_uit(cfg, mp, False)
    aanroepen["mislukt"]["n"] = 0
    r = voer_download_uit(cfg, mp, False)
    assert len(r.opgehaald) == 6 and r.leeg == []


def test_empty_vector_roundtrip(tmp_path):
    from tuinscan.download import schrijf_vector
    from tuinscan.pdok.ogc import haal_features  # noqa: F401

    leeg = gpd.GeoDataFrame({"feature_id": []}, geometry=[], crs=28992)
    pad = tmp_path / "cache" / "vector" / "x.gpkg"
    schrijf_vector(leeg, pad)
    assert len(lees_vector(pad)) == 0


def _manifest(mp):
    from tuinscan.pdok.cache import Manifest

    return Manifest(mp.cache / "manifest.json")


def test_manifest_records_layer_zoom_year_and_ahn_version(tmp_path, aanroepen):
    cfg, mp = maak_cfg(tmp_path)
    voer_download_uit(cfg, mp, False)
    rec = _manifest(mp).lees("zomer_nu")
    assert (rec["laag"], rec["zoom"], rec["jaar"]) == ("2025_ortho25", "14", 2025)
    assert _manifest(mp).lees("ahn_dsm")["versie"] == cfg.ahn.versie
    assert _manifest(mp).lees("bag_pand")["collectie"] == "pand"


@pytest.mark.parametrize(
    "wijzig, opnieuw",
    [
        (lambda d: d["luchtfoto"]["lagen"]["zomer_nu"].update(laag="2024_ortho25"), "zomer_nu"),
        (lambda d: d["luchtfoto"]["lagen"]["cir_vorig"].update(zoom="15"), "cir_vorig"),
        (lambda d: d["luchtfoto"].update(rgb_wmts="https://elders/wmts"), "zomer_nu"),
        (lambda d: d["ahn"].update(dsm_index="https://elders/dsm.json"), "ahn_dsm"),
        (lambda d: d["pdok"]["collecties"].update(pand="pand2"), "bag_pand"),
    ],
)
def test_changed_config_refetches_stale_cache(tmp_path, aanroepen, wijzig, opnieuw):
    cfg, mp = maak_cfg(tmp_path)
    voer_download_uit(cfg, mp, False)
    cfg2, mp2 = maak_cfg(tmp_path, wijzig)
    r = voer_download_uit(cfg2, mp2, False)
    assert opnieuw in r.opgehaald
    assert "bag_vbo" in r.overgeslagen  # de rest blijft uit de cache


def test_changed_ahn_index_url_refreshes_index(tmp_path, aanroepen, monkeypatch):
    vernieuwd = []
    index = gpd.GeoDataFrame({"url": ["k.tif"]}, geometry=[box(0, 0, 1000, 1000)], crs=28992)

    def laad(url, doel, sessie, vernieuw):
        vernieuwd.append((url, vernieuw))
        return index

    monkeypatch.setattr(dl, "_laad_index", laad)
    cfg, mp = maak_cfg(tmp_path)
    voer_download_uit(cfg, mp, False)
    vernieuwd.clear()
    cfg2, mp2 = maak_cfg(tmp_path, lambda d: d["ahn"].update(dtm_index="https://elders/dtm.json"))
    voer_download_uit(cfg2, mp2, False)
    assert vernieuwd == [("https://elders/dtm.json", True)]


def test_old_manifest_without_zoom_is_refetched(tmp_path, aanroepen):
    import json

    cfg, mp = maak_cfg(tmp_path)
    voer_download_uit(cfg, mp, False)
    pad = mp.cache / "manifest.json"
    data = json.loads(pad.read_text(encoding="utf-8"))
    del data["winter_nu"]["zoom"]
    pad.write_text(json.dumps(data), encoding="utf-8")
    assert voer_download_uit(cfg, mp, False).opgehaald == ["winter_nu"]


def test_herkomst_from_manifest_not_config(tmp_path, aanroepen):
    from tuinscan.download import herkomst_uit_cache

    cfg, mp = maak_cfg(tmp_path)
    voer_download_uit(cfg, mp, False)
    for k in ("zomer_nu", "zomer_vorig", "winter_nu", "ahn_dsm"):
        (mp.cache / "raster" / f"{k}.tif").write_bytes(b"x")
    cfg2, _ = maak_cfg(tmp_path, lambda d: d["luchtfoto"]["lagen"]["zomer_nu"].update(
        laag="2026_ortho25", jaar=2026))  # fmt: skip
    h = herkomst_uit_cache(cfg2, mp)
    assert h["zomer_jaar"] == 2025 and h["zomer_vorig_jaar"] == 2024  # wat er echt ligt
    assert h["winter_jaar"] == 2026 and h["ahn_versie"] == cfg.ahn.versie
    assert h["lagen"]["zomer_nu"] == {"laag": "2025_ortho25", "jaar": 2025, "zoom": "14"}
    assert h["ahn_kaartbladen"] == ["k"]
    assert any("zomer_nu" in m and "2026_ortho25" in m for m in h["afwijkingen"])
    (mp.cache / "raster" / "zomer_vorig.tif").unlink()
    assert herkomst_uit_cache(cfg, mp)["zomer_vorig_jaar"] is None
