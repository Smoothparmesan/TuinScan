from pathlib import Path

from tuinscan.pdok.cache import Manifest, vector_pad


def test_manifest_roundtrip_and_bbox_mismatch(tmp_path):
    m = Manifest(tmp_path / "manifest.json")
    a = (1.0, 2.0, 3.0, 4.0)
    b = (1.0, 2.0, 3.0, 5.0)
    assert not m.heeft("pand", a)
    m.registreer("pand", "http://x", a, {"n": 5})
    assert m.heeft("pand", a)
    assert m.heeft("pand", (1.0001, 2, 3, 4))
    assert not m.heeft("pand", b)
    m2 = Manifest(tmp_path / "manifest.json")
    rec = m2.lees("pand")
    assert rec["url"] == "http://x"
    assert rec["n"] == 5
    assert m2.heeft("pand", a)
    assert not list(tmp_path.glob("*.tmp"))


def test_vector_pad():
    assert vector_pad(Path("c"), "pand") == Path("c") / "vector" / "pand.gpkg"
