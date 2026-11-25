from pathlib import Path

import pytest

from tuinscan.cli import main


def test_cli_help_lists_steps(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for stap in ["download", "tuinen", "score", "rapport", "terugkoppeling", "inspecteer"]:
        assert stap in out


def test_cli_refuses_onedrive_data_folder(tmp_path, monkeypatch, capsys):
    import yaml

    od = tmp_path / "OneDrive - Cazas"
    monkeypatch.setenv("OneDriveCommercial", str(od))
    voorbeeld = Path(__file__).parent.parent / "config" / "tuinscan.example.yaml"
    data = yaml.safe_load(voorbeeld.read_text(encoding="utf-8"))
    data["mappen"] = {k: str(od / k) for k in ("invoer", "cache", "uitvoer")}
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert main(["score", "--config", str(cfg)]) == 1
    assert "OneDrive" in capsys.readouterr().err
    assert not od.exists()
