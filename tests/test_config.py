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


def test_ahn_vorige_requires_indexes(tmp_path):
    pad = _schrijf(tmp_path, lambda d: d["ahn"].update(vorige={"versie": "v1"}))
    with pytest.raises(ConfigFout) as e:
        laad_config(pad)
    assert "ahn.vorige" in str(e.value)


def test_per_layer_thresholds_optional_with_fallback(tmp_path):
    from tuinscan.config import laag_drempel

    basis = laad_config(EXAMPLE)
    assert laag_drempel(basis, "cir_nu", "ndvi_drempel") == basis.signalen.ndvi_drempel
    assert laag_drempel(basis, "zomer_vorig", "exg_drempel") == basis.signalen.exg_drempel

    def w(d):
        d["luchtfoto"]["lagen"]["cir_nu"]["ndvi_drempel"] = 0.3
        d["luchtfoto"]["lagen"]["zomer_vorig"]["exg_drempel"] = 0.1

    cfg = laad_config(_schrijf(tmp_path, w))
    assert laag_drempel(cfg, "cir_nu", "ndvi_drempel") == 0.3
    assert laag_drempel(cfg, "cir_vorig", "ndvi_drempel") == cfg.signalen.ndvi_drempel
    assert laag_drempel(cfg, "zomer_vorig", "exg_drempel") == 0.1


@pytest.mark.parametrize(
    "laag, naam, waarde",
    [
        ("zomer_nu", "ndvi_drempel", 0.3),  # NDVI hoort bij de CIR-laag
        ("cir_vorig", "exg_drempel", 0.1),  # ExG hoort bij de RGB-zomerlaag
        ("winter_nu", "exg_drempel", 0.1),  # winter telt niet mee in het masker
        ("cir_nu", "ndvi_drempel", "0.3"),
    ],
)
def test_per_layer_threshold_placement_and_type(tmp_path, laag, naam, waarde):
    pad = _schrijf(tmp_path, lambda d: d["luchtfoto"]["lagen"][laag].update({naam: waarde}))
    with pytest.raises(ConfigFout) as e:
        laad_config(pad)
    assert f"luchtfoto.lagen.{laag}.{naam}" in str(e.value)


def test_hash_includes_per_layer_thresholds(tmp_path):
    basis = parameterset_hash(laad_config(EXAMPLE))
    pad = _schrijf(tmp_path, lambda d: d["luchtfoto"]["lagen"]["cir_vorig"].update(
        ndvi_drempel=0.12))  # fmt: skip
    assert parameterset_hash(laad_config(pad)) != basis
    pad = _schrijf(tmp_path, lambda d: d["luchtfoto"]["lagen"]["zomer_nu"].update(laag="x"))
    assert parameterset_hash(laad_config(pad)) == basis  # laagnaam is herkomst, geen parameter


def test_rijbaan_functies_in_config():
    assert "rijbaan lokale weg" in laad_config(EXAMPLE).pdok.rijbaan_functies


def test_lean_and_halo_keys_in_config_and_hash(tmp_path):
    s = laad_config(EXAMPLE).signalen
    assert s.foto_pandmarge_m == 1.5 and s.s4a_groen_marge_m == 1.0
    assert s.s4a_max_hoogte_m == 4.5 and s.s4a_pandmarge_m == 1.0 and s.s4a_opening_px == 1
    basis = parameterset_hash(laad_config(EXAMPLE))
    pad = _schrijf(tmp_path, lambda d: d["signalen"].update(foto_pandmarge_m=0))
    assert parameterset_hash(laad_config(pad)) != basis


@pytest.mark.parametrize(
    "sleutel, waarde",
    [
        ("foto_pandmarge_m", -1.0),
        ("s4a_pandmarge_m", 11.0),  # verder dan rasters zoekt
        ("s4a_groen_marge_m", -0.5),
        ("s4a_max_hoogte_m", 1.0),  # moet boven s4a_hoogte_m liggen
        ("s4a_opening_px", -1),
    ],
)
def test_lean_and_halo_bounds(tmp_path, sleutel, waarde):
    pad = _schrijf(tmp_path, lambda d: d["signalen"].update({sleutel: waarde}))
    with pytest.raises(ConfigFout, match=rf"signalen\.{sleutel}"):
        laad_config(pad)
