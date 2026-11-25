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


@pytest.mark.parametrize("ronde", ["../x", "a/b", "a\\b", "..", "x..y"])
def test_ronde_with_path_chars_rejected(ronde):
    from tuinscan.config import ConfigFout

    with pytest.raises(ConfigFout, match="ronde"):
        Mappen.uit_config(laad_config(EXAMPLE), ronde)


@pytest.mark.parametrize(
    "ronde", ["a*b", "a?b", 'a"b', "a<b", "a>b", "a|b", "CON", "con", "nul.txt", "COM1", "lpt9",
              "AUX", "PRN", "ronde.", "ronde "],
)  # fmt: skip
def test_ronde_with_windows_invalid_name_rejected(ronde):
    from tuinscan.config import ConfigFout

    with pytest.raises(ConfigFout, match="ronde"):
        Mappen.uit_config(laad_config(EXAMPLE), ronde)


@pytest.mark.parametrize("ronde", ["console", "com10", "wijk-2026_1", "Nul punt"])
def test_ronde_ordinary_names_ok(ronde):
    assert Mappen.uit_config(laad_config(EXAMPLE), ronde).ronde_dir.name == ronde
