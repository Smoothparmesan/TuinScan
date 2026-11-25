from pathlib import Path

import pytest
import yaml

from tuinscan.config import ConfigFout, laad_config, parameterset_hash

EXAMPLE = Path(__file__).parent.parent / "config" / "tuinscan.example.yaml"


def _schrijf(tmp_path, wijzig) -> Path:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    wijzig(data)
    pad = tmp_path / "cfg.yaml"
    pad.write_text(yaml.safe_dump(data), encoding="utf-8")
    return pad


def test_example_config_loads():
    cfg = laad_config(EXAMPLE)
    assert cfg.score["S4a"].gewicht == 25
    assert cfg.gebied.naam == "proefwijk"


def test_weights_must_sum_100(tmp_path):
    pad = _schrijf(tmp_path, lambda d: d["score"]["S1"].update(gewicht=30))
    with pytest.raises(ConfigFout) as e:
        laad_config(pad)
    assert "score" in str(e.value) and "100" in str(e.value)


def test_bbox_must_be_ordered(tmp_path):
    pad = _schrijf(tmp_path, lambda d: d["gebied"].update(bbox_rd=[10, 0, 5, 10]))
    with pytest.raises(ConfigFout, match=r"gebied\.bbox_rd"):
        laad_config(pad)


def test_onder_must_be_below_boven(tmp_path):
    pad = _schrijf(tmp_path, lambda d: d["score"]["S2"].update(onder=1.0, boven=0.5))
    with pytest.raises(ConfigFout, match=r"score\.S2"):
        laad_config(pad)


def test_tuin_delen_only_voor_achter(tmp_path):
    pad = _schrijf(tmp_path, lambda d: d["tuin"].update(delen=["zij"]))
    with pytest.raises(ConfigFout, match=r"tuin\.delen"):
        laad_config(pad)


def test_missing_key_names_key(tmp_path):
    pad = _schrijf(tmp_path, lambda d: d["gebied"].pop("naam"))
    with pytest.raises(ConfigFout, match=r"gebied"):
        laad_config(pad)


def test_hash_changes_with_weights_not_with_paths(tmp_path):
    basis = laad_config(EXAMPLE)
    pad = _schrijf(tmp_path, lambda d: d["mappen"].update(cache="D:/elders/cache"))
    assert parameterset_hash(laad_config(pad)) == parameterset_hash(basis)
    pad2 = _schrijf(
        tmp_path,
        lambda d: (d["score"]["S1"].update(gewicht=20), d["score"]["S5"].update(gewicht=15)),
    )
    h = parameterset_hash(laad_config(pad2))
    assert h != parameterset_hash(basis)
    assert len(h) == 8


@pytest.mark.parametrize(
    "sleutel, wijzig",
    [
        ("score.S1.gewicht", lambda d: d["score"]["S1"].update(gewicht="25")),
        ("gebied.bbox_rd", lambda d: d["gebied"].update(bbox_rd=5)),
        ("score.S2.onder", lambda d: d["score"]["S2"].update(onder="0.05")),
        ("mappen.invoer", lambda d: d["mappen"].update(invoer=None)),
    ],
)
def test_wrong_type_raises_configfout_naming_key(tmp_path, sleutel, wijzig):
    pad = _schrijf(tmp_path, wijzig)
    with pytest.raises(ConfigFout) as e:
        laad_config(pad)
    assert sleutel in str(e.value)
