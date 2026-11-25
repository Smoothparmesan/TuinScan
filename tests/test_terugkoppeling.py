from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import pytest

from tuinscan.config import laad_config
from tuinscan.terugkoppeling import (
    HEADERS,
    kwaliteit,
    lees_terugkoppeling,
    maak_terugkoppelbestand,
    schrijf_kwaliteit,
    selecteer_bezoeklijst,
)

EXAMPLE = Path(__file__).parent.parent / "config" / "tuinscan.example.yaml"


@pytest.fixture
def cfg():
    return laad_config(EXAMPLE)


def _scores(n=30, wijken=("A", "B")):
    rijen = []
    for w in wijken:
        for i in range(n):
            rijen.append({
                "vhe_nummer": f"{'1' if w == 'A' else '2'}{i:05d}", "adres": f"x {i}",
                "wijk": w, "complex": "c", "score": 100.0 - i, "rang_in_wijk": i + 1,
                "reden_1": "r1", "reden_2": None, "reden_codes": ["S3"],
            })  # fmt: skip
    return pd.DataFrame(rijen)


def _lijst(cfg, top=3, controle=2):
    return selecteer_bezoeklijst(_scores(), top, controle, cfg.terugkoppeling.seed)


def _vul(pad, waarden):
    wb = openpyxl.load_workbook(pad)
    ws = wb["Terugkoppeling"]
    for r, kol, w in waarden:
        ws.cell(row=r, column=HEADERS.index(kol) + 1, value=w)
    wb.save(pad)


def test_dropdowns_present(tmp_path, cfg):
    pad = maak_terugkoppelbestand(_lijst(cfg), cfg, tmp_path / "t.xlsx")
    wb = openpyxl.load_workbook(pad)
    ws = wb["Terugkoppeling"]
    lijsten = [v for v in ws.data_validations.dataValidation if v.type == "list"]
    assert len(lijsten) == 3
    assert all(v.showErrorMessage and "Lijsten" in v.formula1 for v in lijsten)
    assert wb["Lijsten"].sheet_state == "hidden"
    assert ws.freeze_panes == "A2"


def test_no_free_text_column(tmp_path, cfg):
    pad = maak_terugkoppelbestand(_lijst(cfg), cfg, tmp_path / "t.xlsx")
    ws = openpyxl.load_workbook(pad)["Terugkoppeling"]
    koppen = [c.value for c in ws[1]]
    assert koppen == [
        "Groep", "Rang", "VHE", "Adres", "Wijk", "Score", "Reden 1", "Reden 2",
        "Redencodes", "Bezoekdatum", "Oordeel", "Oorzaak", "Actie",
    ]  # fmt: skip


def test_vhe_kept_as_text(tmp_path, cfg):
    lijst = _lijst(cfg)
    lijst.loc[lijst.index[0], "vhe_nummer"] = "000123"
    pad = maak_terugkoppelbestand(lijst, cfg, tmp_path / "t.xlsx")
    ws = openpyxl.load_workbook(pad)["Terugkoppeling"]
    assert ws["C2"].value == "000123"
    assert ws["C2"].number_format == "@"
    _vul(pad, [(2, "Oordeel", "terecht")])
    terug, msgs = lees_terugkoppeling(pad, cfg)
    assert terug["vhe_nummer"].tolist() == ["000123"] and msgs == []


def test_read_resorted_and_blank_rows(tmp_path, cfg):
    pad = maak_terugkoppelbestand(_lijst(cfg), cfg, tmp_path / "t.xlsx")
    _vul(pad, [(2, "Oordeel", " Terecht "), (4, "Oordeel", "ONTERECHT")])
    wb = openpyxl.load_workbook(pad)
    ws = wb["Terugkoppeling"]
    ws.insert_rows(3)  # lege rij
    a, b = ws.cell(row=2, column=3).value, ws.cell(row=5, column=3).value
    ws.cell(row=2, column=3).value, ws.cell(row=5, column=3).value = b, a
    wb.save(pad)
    terug, msgs = lees_terugkoppeling(pad, cfg)
    assert msgs == []
    assert len(terug) == 2
    assert set(terug["oordeel"]) == {"terecht", "onterecht"}
    assert terug.set_index("vhe_nummer").loc[b, "oordeel"] == "terecht"


def test_vhe_number_read_back(tmp_path, cfg):
    pad = maak_terugkoppelbestand(_lijst(cfg), cfg, tmp_path / "t.xlsx")
    _vul(pad, [(2, "Oordeel", "terecht")])
    wb = openpyxl.load_workbook(pad)
    wb["Terugkoppeling"]["C2"].value = 100000.0
    wb.save(pad)
    terug, _ = lees_terugkoppeling(pad, cfg)
    assert terug["vhe_nummer"].tolist() == ["100000"]


def test_invalid_value_reported(tmp_path, cfg):
    pad = maak_terugkoppelbestand(_lijst(cfg), cfg, tmp_path / "t.xlsx")
    _vul(pad, [(2, "Oordeel", "terecht"), (3, "Oordeel", "onterecht"),
               (3, "Oorzaak", "kapotte schutting"), (4, "Oordeel", "terecht")])  # fmt: skip
    terug, msgs = lees_terugkoppeling(pad, cfg)
    assert msgs == ["rij 3: 'kapotte schutting' is geen geldige Oorzaak"]
    assert len(terug) == 2


def test_precision_per_signal():
    scores = pd.DataFrame({
        "vhe_nummer": list("abcdef"),
        "reden_codes": [["S3"], ["S3"], ["S3", "S2"], ["S3"], ["S2"], ["S3"]],
    })  # fmt: skip
    terug = pd.DataFrame({
        "vhe_nummer": list("abcd"), "groep": ["top"] * 4,
        "oordeel": ["terecht", "terecht", "terecht", "onterecht"],
    })  # fmt: skip
    kw = kwaliteit(scores, terug).set_index("onderdeel")
    assert kw.loc["S3", "n_beoordeeld"] == 4
    assert kw.loc["S3", "precisie"] == 0.75
    assert kw.loc["S2", "n_beoordeeld"] == 1
    assert kw.loc["top_n", "n_beoordeeld"] == 4


def test_unassessed_signal_has_nan_precision():
    scores = pd.DataFrame({"vhe_nummer": ["a", "b"], "reden_codes": [["S3"], ["S2"]]})
    terug = pd.DataFrame({"vhe_nummer": ["a"], "groep": ["top"], "oordeel": ["terecht"]})
    kw = kwaliteit(scores, terug).set_index("onderdeel")
    assert kw.loc["S2", "n_beoordeeld"] == 0 and np.isnan(kw.loc["S2", "precisie"])


def test_top20_criterion_text(capsys, tmp_path):
    scores = pd.DataFrame({"vhe_nummer": [str(i) for i in range(20)],
                           "reden_codes": [["S3"]] * 20})  # fmt: skip
    terug = pd.DataFrame({
        "vhe_nummer": scores["vhe_nummer"], "groep": "top",
        "oordeel": ["terecht"] * 10 + ["onterecht"] * 10,
    })  # fmt: skip
    schrijf_kwaliteit(kwaliteit(scores, terug), 20, tmp_path / "kwaliteit.csv")
    out = capsys.readouterr().out
    assert "Top-20: 10 van 20 terecht" in out and "GEHAALD" in out and "NIET" not in out
    assert (tmp_path / "kwaliteit.csv").exists()


def test_control_group_deterministic():
    a = selecteer_bezoeklijst(_scores(), 3, 5, 7)
    b = selecteer_bezoeklijst(_scores(), 3, 5, 7)
    assert a["vhe_nummer"].tolist() == b["vhe_nummer"].tolist()
    top = set(a.loc[a.groep == "top", "vhe_nummer"])
    ctrl = a.loc[a.groep == "controle", "vhe_nummer"].tolist()
    assert len(top) == 6 and len(ctrl) == 5 and not top & set(ctrl)
    assert ctrl == sorted(ctrl)


def _ronde(tmp_path, cfg):
    import yaml

    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    data["mappen"] = {k: str(tmp_path / k) for k in ("invoer", "cache", "uitvoer")}
    data["terugkoppeling"]["top_n"] = 3
    pad = tmp_path / "cfg.yaml"
    pad.write_text(yaml.safe_dump(data), encoding="utf-8")
    rd = tmp_path / "uitvoer" / data["gebied"]["naam"]
    rd.mkdir(parents=True)
    _scores().to_parquet(rd / "scores.parquet")
    return pad, rd


def test_cli_maak_lees_and_overwrite_guard(tmp_path, cfg, capsys):
    from tuinscan.cli import main

    pad, rd = _ronde(tmp_path, cfg)
    assert main(["terugkoppeling", "maak", "--config", str(pad)]) == 0
    _vul(rd / "terugkoppeling.xlsx", [(2, "Oordeel", "terecht"), (3, "Oorzaak", "x"),
                                      (3, "Oordeel", "terecht")])  # fmt: skip
    assert main(["terugkoppeling", "maak", "--config", str(pad)]) == 1
    assert main(["terugkoppeling", "maak", "--config", str(pad), "--overschrijf"]) == 0
    _vul(rd / "terugkoppeling.xlsx", [(2, "Oordeel", "terecht"), (3, "Oorzaak", "x"),
                                      (3, "Oordeel", "terecht")])  # fmt: skip
    capsys.readouterr()
    assert main(["terugkoppeling", "lees", "--config", str(pad)]) == 0
    out = capsys.readouterr().out
    assert "rij 3: 'x' is geen geldige Oorzaak" in out and "Top-3: 1 van 1 terecht" in out
    assert (rd / "kwaliteit.csv").exists()
    assert main(["terugkoppeling", "lees", "--config", str(pad), "--bestand", "nope.xlsx"]) == 1
