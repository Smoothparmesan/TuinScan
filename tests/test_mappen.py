from pathlib import Path

import pytest

from tuinscan.config import laad_config
from tuinscan.mappen import Mappen, OneDriveFout, controleer_buiten_onedrive

EXAMPLE = Path(__file__).parent.parent / "config" / "tuinscan.example.yaml"


def test_onedrive_refused(tmp_path):
    env = {"OneDriveCommercial": str(tmp_path / "OneDrive - Cazas")}
    with pytest.raises(OneDriveFout):
        controleer_buiten_onedrive([tmp_path / "OneDrive - Cazas" / "data"], env)


def test_onedrive_ok_elsewhere(tmp_path):
    env = {"OneDriveCommercial": str(tmp_path / "OneDrive - Cazas")}
    controleer_buiten_onedrive([tmp_path / "data"], env)


def test_onedrive_path_part_refused(tmp_path):
    with pytest.raises(OneDriveFout):
        controleer_buiten_onedrive([tmp_path / "OneDrive" / "x"], {})


def test_uit_config_ronde_default_and_explicit():
    cfg = laad_config(EXAMPLE)
    m = Mappen.uit_config(cfg, None)
    assert m.ronde_dir == m.uitvoer / "proefwijk"
    assert Mappen.uit_config(cfg, "r2").ronde_dir == m.uitvoer / "r2"
