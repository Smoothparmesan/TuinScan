import logging

import pytest
import yaml
from openpyxl import Workbook

from tuinscan.config import ConfigFout, laad_config
from tuinscan.fouten import Foutenlijst
from tuinscan.mappen import Mappen
from tuinscan.tobias import (
    laad_mapping,
    lees_ruwe_export,
    lees_vhes,
    normaliseer_bag_id,
    schrijf_vhes,
    stel_mapping_voor,
)

VOORBEELD = __import__("pathlib").Path(__file__).parent.parent / "config" / "tuinscan.example.yaml"


def maak_xlsx(pad, rijen):
    wb = Workbook()
    ws = wb.active
    for r in rijen:
        ws.append(r)
    wb.save(pad)
    return pad


def maak_omgeving(tmp_path, rijen, mapping, filters=None):
    data = yaml.safe_load(VOORBEELD.read_text(encoding="utf-8"))
    data["mappen"] = {k: str(tmp_path / k) for k in ("invoer", "cache", "uitvoer")}
    if filters:
        data["tobias"]["filters"].update(filters)
    cfgpad = tmp_path / "cfg.yaml"
    cfgpad.write_text(yaml.safe_dump(data), encoding="utf-8")
    cfg = laad_config(cfgpad)
    mappen = Mappen.uit_config(cfg, "t")
    mappen.invoer.mkdir(parents=True)
    maak_xlsx(mappen.invoer / cfg.tobias.bestand, rijen)
    (mappen.invoer / cfg.tobias.kolommen_bestand).write_text(
        yaml.safe_dump(mapping), encoding="utf-8"
    )
    return cfg, mappen


KOP = ["VHE", "BAG", "Wijk", "Complex", "Type", "Straat", "Nr", "Toev"]
MAP = {
    "vhe_nummer": "VHE",
    "bag_vbo_id": "BAG",
    "wijk": "Wijk",
    "complex": "Complex",
    "woningtype": "Type",
    "straat": "Straat",
    "huisnummer": "Nr",
    "toevoeging": "Toev",
}
BAG1 = 363010000123456
BAG2 = 363010000123457


def test_bag_id_from_number():
    assert normaliseer_bag_id(363010000123456) == "0363010000123456"


def test_bag_id_float_valid_or_none():
    assert normaliseer_bag_id(363010000123456.0) == "0363010000123456"
    assert normaliseer_bag_id(363010000123456.5) is None


def test_bag_id_strings():
    assert normaliseer_bag_id(" 363010000123456 ") == "0363010000123456"
    assert normaliseer_bag_id("3.63010000123456e+14") == "0363010000123456"
    assert normaliseer_bag_id("nan") is None
    assert normaliseer_bag_id("") is None
    assert normaliseer_bag_id(None) is None
    assert normaliseer_bag_id("12345") is None


def test_bag_id_rejects_non_vbo():
    assert normaliseer_bag_id("0363100012345678") is None  # type code 10 = pand


def test_header_detected_below_title(tmp_path):
    p = maak_xlsx(
        tmp_path / "x.xlsx",
        [["Export Tobias 25-11-2026"], [], ["VHE", "Wijk", "Type"], ["1", "Noord", "rij"]],
    )
    df = lees_ruwe_export(p, None, None)
    assert list(df.columns) == ["VHE", "Wijk", "Type"]
    assert df.iloc[0]["VHE"] == "1"


def test_explicit_kopregel_and_blad(tmp_path):
    p = maak_xlsx(tmp_path / "x.xlsx", [["a", "b", "c"], ["VHE", "Wijk", "Type"], ["1", "N", "r"]])
    df = lees_ruwe_export(p, None, 2)
    assert list(df.columns) == ["VHE", "Wijk", "Type"]
    with pytest.raises(ConfigFout):
        lees_ruwe_export(p, "bestaatniet", None)


def test_suggest_by_pattern_with_odd_headers(tmp_path):
    p = maak_xlsx(
        tmp_path / "x.xlsx",
        [["Kol A", "Kol B", "Kol C"], ["x", BAG1, "1234 AB"], ["y", BAG2, "5678 cd"]],
    )
    v = stel_mapping_voor(lees_ruwe_export(p, None, None))
    assert v["bag_vbo_id"].kolom == "Kol B" and v["bag_vbo_id"].zekerheid == "hoog"
    assert v["postcode"].kolom == "Kol C" and v["postcode"].zekerheid == "hoog"
    assert v["wijk"].kolom is None and v["wijk"].zekerheid == "laag"


def test_suggest_by_alias(tmp_path):
    p = maak_xlsx(
        tmp_path / "x.xlsx",
        [["Verhuureenheid", "Buurt", "Overig"], ["a1", "Noord", "q"], ["a2", "Zuid", "r"]],
    )
    v = stel_mapping_voor(lees_ruwe_export(p, None, None))
    assert v["vhe_nummer"].kolom == "Verhuureenheid" and v["vhe_nummer"].zekerheid == "middel"
    assert v["wijk"].kolom == "Buurt"


def test_column_suggested_for_one_field_only(tmp_path):
    p = maak_xlsx(tmp_path / "x.xlsx", [["Type", "Status", "Z"], ["a", "b", "c"]])
    v = stel_mapping_voor(lees_ruwe_export(p, None, None))
    gebruikt = [x.kolom for x in v.values() if x.kolom]
    assert len(gebruikt) == len(set(gebruikt))


def test_missing_mapping_lists_headers(tmp_path):
    m = {k: v for k, v in MAP.items() if k != "wijk"}
    cfg, mappen = maak_omgeving(tmp_path, [KOP, ["1", BAG1, "N", "C", "t", "S", "1", ""]], m)
    with pytest.raises(ConfigFout) as e:
        lees_vhes(cfg, mappen, Foutenlijst())
    assert "wijk" in str(e.value) and "Complex" in str(e.value)


def test_mapping_header_not_in_export(tmp_path):
    m = dict(MAP, wijk="Bestaatniet")
    cfg, mappen = maak_omgeving(tmp_path, [KOP, ["1", BAG1, "N", "C", "t", "S", "1", ""]], m)
    with pytest.raises(ConfigFout) as e:
        lees_vhes(cfg, mappen, Foutenlijst())
    assert "Bestaatniet" in str(e.value)


def test_laad_mapping_needs_address(tmp_path):
    p = tmp_path / "k.yaml"
    m = {k: v for k, v in MAP.items() if k not in ("straat", "huisnummer", "toevoeging")}
    p.write_text(yaml.safe_dump(m), encoding="utf-8")
    with pytest.raises(ConfigFout, match="adres"):
        laad_mapping(p)


def test_invalid_bag_id_to_foutenlijst(tmp_path):
    rijen = [KOP, ["1", BAG1, "N", "C", "t", "S", 1, ""], ["2", 12345, "N", "C", "t", "S", 2, ""]]
    cfg, mappen = maak_omgeving(tmp_path, rijen, MAP)
    fouten = Foutenlijst()
    df = lees_vhes(cfg, mappen, fouten)
    assert list(df["vhe_nummer"]) == ["1"]
    assert fouten.vhe_nummers() == {"2"}
    assert "exporteer de kolom als tekst" in fouten.regels[0][2]


def test_empty_bag_id_to_foutenlijst(tmp_path):
    rijen = [KOP, ["1", BAG1, "N", "C", "t", "S", 1, ""], ["2", None, "N", "C", "t", "S", 2, ""]]
    cfg, mappen = maak_omgeving(tmp_path, rijen, MAP)
    fouten = Foutenlijst()
    df = lees_vhes(cfg, mappen, fouten)
    assert len(df) == 1 and fouten.vhe_nummers() == {"2"}


def test_duplicate_vhe_keeps_first(tmp_path):
    rijen = [
        KOP,
        ["1", BAG1, "N", "C", "t", "S", 1, ""],
        ["1", BAG2, "N", "C", "t", "S", 2, ""],
    ]
    cfg, mappen = maak_omgeving(tmp_path, rijen, MAP)
    fouten = Foutenlijst()
    df = lees_vhes(cfg, mappen, fouten)
    assert len(df) == 1 and df.iloc[0]["bag_vbo_id"] == "0363010000123456"
    assert fouten.aantal == 1 and "dubbel VHE-nummer" in fouten.regels[0][2]


def test_filters_applied_only_when_mapped(tmp_path, caplog):
    rijen = [
        KOP,
        ["1", BAG1, "N", "C", "Eengezinswoning", "S", 1, ""],
        ["2", BAG2, "N", "C", "Flat", "S", 2, ""],
    ]
    cfg, mappen = maak_omgeving(
        tmp_path,
        rijen,
        MAP,
        filters={"woningtype": ["eengezinswoning"], "status_uitsluiten": ["leeg"]},
    )
    with caplog.at_level(logging.WARNING):
        df = lees_vhes(cfg, mappen, Foutenlijst())
    assert list(df["vhe_nummer"]) == ["1"]
    assert "status" in caplog.text


def test_exclusion_and_inclusion_filters_with_optionals(tmp_path):
    kop = KOP + ["Tuin", "Status"]
    rijen = [
        kop,
        ["1", BAG1, "N", "C", "t", "S", 1, "", "Ja", "verhuurd"],
        ["2", BAG2, "N", "C", "t", "S", 2, "", "Nee", "verhuurd"],
        ["3", 363010000123458, "N", "C", "t", "S", 3, "", "Ja", "Leeg"],
    ]
    m = dict(MAP, eigen_tuin="Tuin", status="Status")
    cfg, mappen = maak_omgeving(
        tmp_path, rijen, m, filters={"eigen_tuin_ja": ["ja"], "status_uitsluiten": ["leeg"]}
    )
    df = lees_vhes(cfg, mappen, Foutenlijst())
    assert list(df["vhe_nummer"]) == ["1"]
    assert {"eigen_tuin", "status"} <= set(df.columns)


def test_address_composed(tmp_path):
    rijen = [KOP, ["1", BAG1, "N", "C", "t", "Lindelaan", 12, "A"]]
    cfg, mappen = maak_omgeving(tmp_path, rijen, MAP)
    df = lees_vhes(cfg, mappen, Foutenlijst())
    assert df.iloc[0]["adres"] == "Lindelaan 12A"


def test_adres_wins_over_parts(tmp_path):
    rijen = [KOP + ["Adres"], ["1", BAG1, "N", "C", "t", "Lindelaan", 12, "A", "Elzenlaan 3"]]
    cfg, mappen = maak_omgeving(tmp_path, rijen, dict(MAP, adres="Adres"))
    df = lees_vhes(cfg, mappen, Foutenlijst())
    assert df.iloc[0]["adres"] == "Elzenlaan 3"


def test_writes_parquet_with_columns(tmp_path):
    import pandas as pd

    rijen = [KOP, ["1", BAG1, "N", "C", "t", "Lindelaan", 12, ""]]
    cfg, mappen = maak_omgeving(tmp_path, rijen, MAP)
    df = lees_vhes(cfg, mappen, Foutenlijst())
    assert list(df.columns[:6]) == [
        "vhe_nummer", "bag_vbo_id", "adres", "wijk", "complex", "woningtype",
    ]  # fmt: skip
    assert not (mappen.ronde_dir / "vhe.parquet").exists()  # schrijven doet de stap
    schrijf_vhes(df, tmp_path / "vhe.parquet")
    pq = pd.read_parquet(tmp_path / "vhe.parquet")
    assert list(pq["vhe_nummer"]) == ["1"]


def test_bag_id_numpy_float():
    import numpy as np

    assert normaliseer_bag_id(np.float64(363010000123456.0)) == "0363010000123456"


def test_huisnummer_alias_beats_numeric_column(tmp_path):
    p = maak_xlsx(
        tmp_path / "x.xlsx",
        [["Bouwjaar", "Huisnr", "Z"], [1965, 12, "a"], [1970, 14, "b"]],
    )
    v = stel_mapping_voor(lees_ruwe_export(p, None, None))
    assert v["huisnummer"].kolom == "Huisnr"


def test_excel_float_strings_in_bag_column(tmp_path):
    rijen = [KOP, ["1", "3.63010000123456e+14", "N", "C", "t", "S", 1, ""]]
    cfg, mappen = maak_omgeving(tmp_path, rijen, MAP)
    df = lees_vhes(cfg, mappen, Foutenlijst())
    assert list(df["bag_vbo_id"]) == ["0363010000123456"]


def test_fout_rows_use_excel_row_numbers(tmp_path):
    rijen = [
        ["Export"],
        [],
        KOP,
        ["1", BAG1, "N", "C", "t", "S", 1, ""],
        ["1", BAG2, "N", "C", "t", "S", 2, ""],
        [None, BAG2, "N", "C", "t", "S", 3, ""],
    ]
    cfg, mappen = maak_omgeving(tmp_path, rijen, MAP)
    fouten = Foutenlijst()
    lees_vhes(cfg, mappen, fouten)
    assert "rij 5 genegeerd, eerste rij behouden" in fouten.regels[0][2]
    assert fouten.regels[1][0] == "rij 6"
