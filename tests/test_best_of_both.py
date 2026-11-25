"""Regressietests voor de gecombineerde 'best of both' review-fixes."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import geopandas as gpd
import numpy as np
import openpyxl
import pandas as pd
import pytest
import rasterio
import requests
import responses
import yaml
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, Polygon

from miniwijk import VOORBEELD, rd, rd_box
from tuinscan import download as dl
from tuinscan import stappen, tobias
from tuinscan.cli import main
from tuinscan.config import ConfigFout, laad_config
from tuinscan.download import schrijf_vector
from tuinscan.fouten import Foutenlijst
from tuinscan.kalibreer import _tegels
from tuinscan.koppeling import koppel
from tuinscan.pdok import ogc
from tuinscan.pdok.ahn import gdal_proxy_opties, lees_ahn
from tuinscan.pdok.cache import vector_pad
from tuinscan.rasters import (
    TuinRasters,
    bouw_tuinrasters,
    bronnen_uit_cache,
    open_bronnen,
    overhang_zone,
)
from tuinscan.signalen import s3_overhang, s4a_hoog_object, s4a_regios
from tuinscan.stappen import stap_rapport, stap_score, stap_tuinen
from tuinscan.terugkoppeling import (
    lees_terugkoppeling,
    maak_terugkoppelbestand,
    selecteer_bezoeklijst,
)
from tuinscan.tobias import ALGEMENE_VHE, _leeg, lees_ruwe_export
from tuinscan.tuinvlak import _halfvlakken, maak_tuinen
from tuinscan.uitsneden import maak_uitsneden, veilige_bestandsnaam
from tuinscan.voorbeeld import maak_voorbeeld_export

RES = 0.25
GROEN = (60, 140, 50)
GRIJS = (128, 128, 128)
ZWART = (0, 0, 0)


def _schrijf_cfg(tmp_path: Path, wijzig) -> Path:
    data = yaml.safe_load(VOORBEELD.read_text(encoding="utf-8"))
    wijzig(data)
    pad = tmp_path / "cfg.yaml"
    pad.write_text(yaml.safe_dump(data), encoding="utf-8")
    return pad


def _beeld(kleur, n: int = 10, banden: int = 3) -> np.ma.MaskedArray:
    data = np.empty((banden, n, n), dtype=np.float32)
    data[:] = np.asarray(kleur, dtype=np.float32).reshape(banden, 1, 1)
    return np.ma.masked_array(data, mask=np.zeros_like(data, dtype=bool))


def _hoogte(n: int = 10, waarde: float = 0.0) -> np.ma.MaskedArray:
    return _beeld((waarde,), n, banden=1)


def _tr(n: int = 10, **kw) -> TuinRasters:
    velden = dict(
        transform=from_origin(120000, 485000 + n * RES, RES, RES),
        vorm=(n, n),
        tuin_masker=np.ones((n, n), dtype=bool),
        zone_masker=np.zeros((n, n), dtype=bool),
        zomer_nu=_beeld(GRIJS, n),
        zomer_vorig=None,
        cir_nu=None,
        cir_vorig=None,
        ndsm=None,
        ndsm_vorig=None,
        pand_afstand=None,
    )
    velden.update(kw)
    return TuinRasters(**velden)


# --- 1. Uitsneden: Windows-veilige bestandsnamen ---


def test_veilige_bestandsnaam_windows_and_collision_safe():
    assert veilige_bestandsnaam("12345") == "12345"
    assert veilige_bestandsnaam("VHE-01_a") == "VHE-01_a"
    a, b = veilige_bestandsnaam("100/1"), veilige_bestandsnaam("100:1")
    assert a != b and "/" not in a and ":" not in b
    for vies in ("../geheim", "CON", "nul.txt", "a*b?c", "  "):
        veilig = veilige_bestandsnaam(vies)
        assert ".." not in veilig and "/" not in veilig and "\\" not in veilig
        assert veilig.split(".")[0].upper() not in {"CON", "NUL"}


def test_maak_uitsneden_sanitizes_slash_vhe(miniwijk, tmp_path):
    bronnen = bronnen_uit_cache(miniwijk.mappen)
    uit = maak_uitsneden("../100/1", rd_box((0, 18, 6, 32)), bronnen, tmp_path)
    pad = uit["thumb_zomer_nu"]
    assert pad is not None and pad.exists()
    assert pad.parent.resolve() == (tmp_path / "uitsneden").resolve()


# --- 2. KPI sentinel "*" ---


def test_rounding_sentinel_does_not_inflate_n_vhe(miniwijk, monkeypatch):
    echt = tobias.lees_vhes

    def met_waarschuwing(cfg, mappen, fouten):
        df = echt(cfg, mappen, fouten)
        fouten.voeg_toe(ALGEMENE_VHE, tobias.STAP, "waarschuwing excel-afronding")
        return df

    monkeypatch.setattr(tobias, "lees_vhes", met_waarschuwing)
    r = stap_tuinen(miniwijk.cfg, miniwijk.mappen)
    assert (r.n_vhe, r.n_tuin, r.percentage) == (4, 4, 100.0)


# --- 3. Enkele berging-aftrek per VHE ---


def test_negeer_berging_deducted_only_once_across_voor_and_achter(miniwijk, monkeypatch):
    mw = miniwijk
    cfg = dataclasses.replace(
        mw.cfg, tuin=dataclasses.replace(mw.cfg.tuin, delen=["voor", "achter"])
    )
    stap_tuinen(cfg, mw.mappen)
    vhe = pd.read_parquet(mw.mappen.ronde_dir / "vhe.parquet")
    vhe["standaardberging"] = "Ja"
    vhe.to_parquet(mw.mappen.ronde_dir / "vhe.parquet", index=False)

    # Beide tuindelen hebben een berging-kandidaat (voor: 4 m², achter: 6 m²)
    groottes = iter([[(4.0, 4.0)], [(6.0, 6.0)]] * 10)
    monkeypatch.setattr(stappen, "s4a_regios", lambda tr, c: next(groottes))
    per_aanroep = []
    echt = stappen.bereken_signalen

    def spion(tr, c, negeer):
        per_aanroep.append(negeer)
        return echt(tr, c, negeer)

    monkeypatch.setattr(stappen, "bereken_signalen", spion)
    stap_score(cfg, mw.mappen)
    # 3 rijwoningen zonder bijgebouw (elk 2 delen): precies 1 True per woning
    assert sum(per_aanroep) == 3


# --- 4. Geometrie: _halfvlakken, multi-VBO-anker ---


def test_halfvlakken_handles_duplicate_vertices():
    doos = rd_box((0, 0, 20, 20))
    muur = LineString([rd(10, 5), rd(10, 5), rd(10, 15), rd(10, 15)])
    helften = _halfvlakken(muur, doos)
    assert helften is not None and len(helften) == 2
    assert abs(helften[0].area - 200) < 1e-6
    assert _halfvlakken(LineString([rd(10, 5), rd(10, 5)]), doos) is None


def test_multi_vbo_l_shaped_pand_anchors_per_house(miniwijk):
    l_pand = Polygon([
        rd(0, 8), rd(16, 8), rd(16, 14), rd(6, 14), rd(6, 26), rd(0, 26), rd(0, 8),
    ])
    perceel = rd_box((-2, 2, 18, 30))
    id1, id2 = "0363010000000101", "0363010000000102"
    pgdf = gpd.GeoDataFrame(
        {"feature_id": ["p-L"], "identificatie": ["0363100000000777"]},
        geometry=[l_pand],
        crs=28992,
    )
    vgdf = gpd.GeoDataFrame(
        {
            "feature_id": ["v-1", "v-2"],
            "identificatie": [id1, id2],
            "gebruiksdoel": ["woonfunctie", "woonfunctie"],
        },
        geometry=[Point(rd(12, 11)), Point(rd(3, 22))],
        crs=28992,
    )
    percelen = gpd.GeoDataFrame(
        {"feature_id": ["c-L"], "identificatie_lokaal_id": ["999"]},
        geometry=[perceel],
        crs=28992,
    )
    gekoppeld = gpd.GeoDataFrame(
        {
            "vhe_nummer": ["H1", "H2"],
            "bag_vbo_id": [id1, id2],
            "pand_id": ["0363100000000777", "0363100000000777"],
            "perceel_id": ["999", "999"],
            "adres": ["A 1", "A 2"],
            "wijk": ["W", "W"],
            "complex": ["C", "C"],
        },
        geometry=[Point(rd(12, 11)), Point(rd(3, 22))],
        crs=28992,
    )
    tuinen = maak_tuinen(
        gekoppeld, vgdf, pgdf, percelen, miniwijk.wegdelen, miniwijk.cfg, Foutenlijst()
    )
    h1_voor = tuinen[(tuinen["vhe_nummer"] == "H1") & (tuinen["deel"] == "voor")]
    assert len(h1_voor) == 1
    assert h1_voor.geometry.iloc[0].bounds[3] <= rd(0, 14)[1] + 1e-6


# --- 5. Signalen: S3 boomkruin-cap & volledige schaduw ---


def test_s3_filters_tall_street_tree_canopy():
    cfg = laad_config(VOORBEELD)
    n = 10
    zone = np.ones((n, n), dtype=bool)
    kroon = _tr(n, zone_masker=zone, zomer_nu=_beeld(GROEN, n), ndsm=_hoogte(n, 7.0))
    assert s3_overhang(kroon, cfg) == 0.0
    uit_cfg = dataclasses.replace(
        cfg, signalen=dataclasses.replace(cfg.signalen, s3_max_hoogte_m=0.0)
    )
    assert s3_overhang(kroon, uit_cfg) == pytest.approx(n * n * RES**2)


def test_s3_and_s4a_full_shadow_return_none():
    cfg = laad_config(VOORBEELD)
    n = 10
    zone = np.ones((n, n), dtype=bool)
    tr = _tr(n, zone_masker=zone, zomer_nu=_beeld(ZWART, n), ndsm=_hoogte(n, 3.0))
    assert s3_overhang(tr, cfg) is None
    assert s4a_hoog_object(tr, cfg, False) is None
    assert s4a_regios(tr, cfg) is None


# --- 6. Config: optionele defaults, filters, grenzen ---


def test_config_optional_s3_max_hoogte_and_unknown_filter_rejected(tmp_path):
    pad_oud = _schrijf_cfg(tmp_path, lambda d: d["signalen"].pop("s3_max_hoogte_m", None))
    assert laad_config(pad_oud).signalen.s3_max_hoogte_m == 4.5

    pad_filter = _schrijf_cfg(tmp_path, lambda d: d["tobias"]["filters"].update(onbekend=["x"]))
    with pytest.raises(ConfigFout, match=r"tobias\.filters\.onbekend"):
        laad_config(pad_filter)


@pytest.mark.parametrize(
    "sleutel, wijzig",
    [
        ("gebied.buffer_m", lambda d: d["gebied"].update(buffer_m=-1)),
        ("pdok.workers", lambda d: d["pdok"].update(workers=0)),
        ("tuin.max_diepte_m", lambda d: d["tuin"].update(max_diepte_m=0)),
        ("tuin.voorzijde_zoekafstand_m", lambda d: d["tuin"].update(voorzijde_zoekafstand_m=0)),
        ("tuin.min_oppervlak_m2", lambda d: d["tuin"].update(min_oppervlak_m2=-1)),
        ("signalen.resolutie_m", lambda d: d["signalen"].update(resolutie_m=0)),
        ("signalen.schaduw_helderheid", lambda d: d["signalen"].update(schaduw_helderheid=300)),
        ("signalen.s3_max_hoogte_m", lambda d: d["signalen"].update(s3_max_hoogte_m=0.5)),
        ("terugkoppeling.top_n", lambda d: d["terugkoppeling"].update(top_n=0)),
        (
            "terugkoppeling.controlegroep_n",
            lambda d: d["terugkoppeling"].update(controlegroep_n=-1),
        ),
        (
            "score.S1.gewicht",
            lambda d: (d["score"]["S1"].update(gewicht=-10), d["score"]["S5"].update(gewicht=45)),
        ),
    ],
)
def test_config_bounds_checked(tmp_path, sleutel, wijzig):
    pad = _schrijf_cfg(tmp_path, wijzig)
    with pytest.raises(ConfigFout, match=sleutel.replace(".", r"\.")):
        laad_config(pad)


# --- 7. AHN merge, SSL_CERT_FILE, OGC loop/Retry-After, CLI download ---


def test_ahn_merge_respects_different_nodata_and_nan(tmp_path):
    a_pad, b_pad = tmp_path / "a.tif", tmp_path / "b.tif"
    a_data = np.full((1, 20, 20), np.nan, dtype="float32")
    a_data[0, :, :10] = 1.5
    with rasterio.open(
        a_pad, "w", driver="GTiff", width=20, height=20, count=1, dtype="float32",
        crs="EPSG:28992", transform=from_origin(0, 10, 0.5, 0.5), nodata=np.nan,
    ) as d:
        d.write(a_data)
    b_data = np.full((1, 20, 20), -9999.0, dtype="float32")
    b_data[0, :, 10:] = 2.5
    with rasterio.open(
        b_pad, "w", driver="GTiff", width=20, height=20, count=1, dtype="float32",
        crs="EPSG:28992", transform=from_origin(0, 10, 0.5, 0.5), nodata=-9999.0,
    ) as d:
        d.write(b_data)
    uit = lees_ahn([str(a_pad), str(b_pad)], (0.0, 0.0, 10.0, 10.0), tmp_path / "out.tif")
    with rasterio.open(uit) as r:
        arr = r.read(1)
    assert np.allclose(arr[:, :10], 1.5) and np.allclose(arr[:, 10:], 2.5)


def test_gdal_proxy_ssl_cert_file_fallback():
    opt = gdal_proxy_opties({"SSL_CERT_FILE": "/corp/ca.pem"}, systeem={})
    assert opt["GDAL_CURL_CA_BUNDLE"] == "/corp/ca.pem"
    assert opt["CURL_CA_BUNDLE"] == "/corp/ca.pem"


@responses.activate
def test_ogc_loop_guard_and_retry_after(monkeypatch):
    url = "https://example.test/ogc/collections/pand/items"
    sleeps = []
    monkeypatch.setattr(ogc.time, "sleep", sleeps.append)
    responses.get(url, status=429, headers={"Retry-After": "5"})
    responses.get(
        url,
        json={
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "id": "1", "geometry": None, "properties": {}}],
            "links": [{"rel": "next", "href": f"{url}?p=2"}],
        },
    )
    responses.get(
        f"{url}?p=2",
        json={
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "id": "2", "geometry": None, "properties": {}}],
            "links": [{"rel": "next", "href": f"{url}?p=2"}],
        },
    )
    gdf = ogc.haal_features("https://example.test/ogc/", "pand", (1, 2, 3, 4), requests.Session())
    assert len(gdf) == 2 and sleeps == [5.0]


def test_cli_download_returns_1_on_failure(miniwijk, monkeypatch):
    monkeypatch.setattr(
        dl,
        "voer_download_uit",
        lambda *a: dl.DownloadRapport(mislukt={"bag_pand": "netwerk"}),
    )
    assert main(["download", "--config", str(miniwijk.config_pad)]) == 1


# --- 8. Terugkoppeling & Tobias & Rapport ---


def test_terugkoppeling_lean_xml_wijk_sort_and_header_validation(tmp_path):
    cfg = laad_config(VOORBEELD)
    scores = pd.DataFrame(
        [
            {
                "vhe_nummer": f"{w}{i}", "adres": f"S {i}", "wijk": w, "complex": "C",
                "score": 90 - i, "rang_in_wijk": i + 1, "reden_1": "r", "reden_2": None,
                "reden_codes": ["S3"],
            }
            for w in ("B", "A")
            for i in range(3)
        ]
    )
    lijst = selecteer_bezoeklijst(scores, 2, 0, 42)
    assert lijst["wijk"].tolist() == ["A", "A", "B", "B"]
    pad = maak_terugkoppelbestand(lijst, cfg, tmp_path / "t.xlsx")
    wb = openpyxl.load_workbook(pad)
    ws = wb["Terugkoppeling"]
    assert ws.max_row == 5  # geen 1000 lege geformatteerde rijen
    kop = [c.value for c in ws[1]]
    vhe_letter = openpyxl.utils.get_column_letter(kop.index("VHE") + 1)
    datum_letter = openpyxl.utils.get_column_letter(kop.index("Bezoekdatum") + 1)
    # lege rijen (handmatig toegevoegde bezoeken) erven tekst- en datumformaat van de kolom
    assert ws.column_dimensions[vhe_letter].number_format == "@"
    assert ws.column_dimensions[datum_letter].number_format == "dd-mm-yyyy"
    ws.cell(row=1, column=[c.value for c in ws[1]].index("Oordeel") + 1, value="Weg")
    wb.save(pad)
    wb.close()
    with pytest.raises(ValueError, match="Oordeel"):
        lees_terugkoppeling(pad, cfg)


def test_tobias_pd_na_and_corrupt_xlsx(tmp_path):
    assert _leeg(pd.NA) and _leeg("None") and _leeg("<NA>")
    kapot = tmp_path / "kapot.xlsx"
    kapot.write_bytes(b"geen-zip")
    with pytest.raises(ConfigFout):
        lees_ruwe_export(kapot, None, None)


def test_rapport_keeps_ahn_dot_zero_and_prunes_stale_jpg(miniwijk):
    stap_tuinen(miniwijk.cfg, miniwijk.mappen)
    stap_score(miniwijk.cfg, miniwijk.mappen)
    sc_pad = miniwijk.mappen.ronde_dir / "scores.parquet"
    sc = pd.read_parquet(sc_pad)
    sc["ahn_versie"] = "AHN 4.0"
    sc["zomer_jaar"] = 2025.0
    sc.to_parquet(sc_pad, index=False)
    oud = miniwijk.mappen.ronde_dir / "uitsneden" / "999999_detail_zomer_nu.jpg"
    oud.parent.mkdir(parents=True, exist_ok=True)
    oud.write_bytes(b"oud")
    html = stap_rapport(miniwijk.cfg, miniwijk.mappen).read_text(encoding="utf-8")
    assert "AHN 4.0" in html and "zomerfoto 2025 " in html
    assert not oud.exists()


def test_rapport_survives_locked_stale_jpg(miniwijk, monkeypatch):
    stap_tuinen(miniwijk.cfg, miniwijk.mappen)
    stap_score(miniwijk.cfg, miniwijk.mappen)
    oud = miniwijk.mappen.ronde_dir / "uitsneden" / "999999_detail_zomer_nu.jpg"
    oud.parent.mkdir(parents=True, exist_ok=True)
    oud.write_bytes(b"oud")
    echt = Path.unlink

    def vergrendeld(self, missing_ok=False):
        if self.name == oud.name:
            raise PermissionError("in gebruik door een ander proces")
        return echt(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", vergrendeld)
    assert stap_rapport(miniwijk.cfg, miniwijk.mappen).exists()
    assert oud.exists()


# --- 9. Rasters open_bronnen, kalibreer tegels, koppeling & voorbeeld ---


def test_open_bronnen_matches_path_based_rasters(miniwijk):
    mw = miniwijk
    bronnen = bronnen_uit_cache(mw.mappen)
    tuin = rd_box((0, 18, 6, 32))
    voetpad = mw.wegdelen[mw.wegdelen["functie"] == "voetpad"]
    zone = overhang_zone(tuin, mw.panden, voetpad, mw.begroeid, 1.5)
    tr_pad = bouw_tuinrasters(tuin, zone, bronnen, RES, mw.panden)
    with open_bronnen(bronnen, vul_dtm=True) as geopend:
        tr_open = bouw_tuinrasters(tuin, zone, geopend, RES, mw.panden)
    np.testing.assert_allclose(tr_open.zomer_nu.filled(-1), tr_pad.zomer_nu.filled(-1))
    np.testing.assert_allclose(tr_open.ndsm.filled(-999), tr_pad.ndsm.filled(-999), atol=1e-4)


def test_kalibreer_tegels_non_overlapping():
    tegels = list(_tegels((120000.1, 485000.1, 120600.1, 485600.1), RES))
    assert len(tegels) == 9
    for i, a in enumerate(tegels):
        assert all(v % RES == 0 for v in a)
        for b in tegels[i + 1 :]:
            assert min(a[2], b[2]) <= max(a[0], b[0]) or min(a[3], b[3]) <= max(a[1], b[1])


def test_koppeling_missing_href_column_falls_back_spatially(miniwijk):
    mw = miniwijk
    vbo = mw.vbo.drop(columns=["pand.href"])
    f = Foutenlijst()
    r = koppel(mw.vhes, vbo, mw.panden, mw.percelen, mw.cfg.pdok.velden, f)
    assert f.aantal == 0 and len(r) == 4


def test_voorbeeld_excludes_partial_apartment_block(miniwijk):
    mw = miniwijk
    panden = mw.panden.copy()
    panden.loc[panden["identificatie"] == mw.pand_ids["vrijstaand"], "aantal_verblijfsobjecten"] = 4
    schrijf_vector(panden, vector_pad(mw.mappen.cache, "bag_pand"))
    with pytest.raises(stappen.StapFout):
        maak_voorbeeld_export(mw.cfg, mw.mappen)
