from pathlib import Path

import yaml
from openpyxl import Workbook

from tuinscan.cli import main

VOORBEELD = Path(__file__).parent.parent / "config" / "tuinscan.example.yaml"


def opzet(tmp_path):
    data = yaml.safe_load(VOORBEELD.read_text(encoding="utf-8"))
    data["mappen"] = {k: str(tmp_path / k) for k in ("invoer", "cache", "uitvoer")}
    cfgpad = tmp_path / "cfg.yaml"
    cfgpad.write_text(yaml.safe_dump(data), encoding="utf-8")
    invoer = tmp_path / "invoer"
    invoer.mkdir()
    wb = Workbook()
    ws = wb.active
    ws.append(["Titel"])
    ws.append(["Kol A", "Kol B", "Kol C"])
    ws.append(["x" * 50, 363010000123456, "1234 AB"])
    ws.append(["y", 363010000123457, "5678 CD"])
    wb.save(invoer / data["tobias"]["bestand"])
    return cfgpad, invoer


def test_inspecteer_prints_and_writes_mapping(tmp_path, capsys):
    cfgpad, invoer = opzet(tmp_path)
    assert main(["inspecteer", "--config", str(cfgpad)]) == 0
    out = capsys.readouterr().out
    assert "Kol B" in out and "bag_vbo_id" in out and "hoog" in out
    assert "x" * 50 not in out
    m = yaml.safe_load((invoer / "kolommen.yaml").read_text(encoding="utf-8"))
    assert m["bag_vbo_id"] == "Kol B" and m["postcode"] == "Kol C"
    assert not (invoer / "kolommen.voorstel.yaml").exists()


def test_inspecteer_does_not_overwrite(tmp_path):
    cfgpad, invoer = opzet(tmp_path)
    (invoer / "kolommen.yaml").write_text("wijk: Mijn\n", encoding="utf-8")
    assert main(["inspecteer", "--config", str(cfgpad)]) == 0
    assert (invoer / "kolommen.yaml").read_text(encoding="utf-8") == "wijk: Mijn\n"
    assert (invoer / "kolommen.voorstel.yaml").exists()


def test_inspecteer_missing_export(tmp_path, capsys):
    cfgpad, invoer = opzet(tmp_path)
    (invoer / "tobias_export.xlsx").unlink()
    assert main(["inspecteer", "--config", str(cfgpad)]) == 1
    assert "tobias_export.xlsx" in capsys.readouterr().err
