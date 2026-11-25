import csv

from tuinscan.fouten import Foutenlijst


def test_foutenlijst_csv(tmp_path):
    f = Foutenlijst()
    f.voeg_toe("V1", "koppeling", "geen BAG-id")
    f.voeg_toe("V2", "tuinen", "geen perceel")
    pad = tmp_path / "foutenlijst.csv"
    f.schrijf(pad)
    assert pad.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM: Excel leest UTF-8 goed
    rijen = list(csv.reader(pad.open(encoding="utf-8-sig", newline=""), delimiter=";"))
    assert rijen[0] == ["vhe_nummer", "stap", "reden"]
    assert len(rijen) == 3
    assert f.aantal == 2
    assert f.vhe_nummers() == {"V1", "V2"}


def test_foutenlijst_roundtrip_with_semicolons_and_m2(tmp_path):
    from tuinscan.fouten import lees_foutenlijst

    f = Foutenlijst()
    f.voeg_toe("V1", "tuinen", "tuinvlak te klein (voor: 3 m² < 5); zie kaart")
    pad = tmp_path / "foutenlijst.csv"
    f.schrijf(pad)
    df = lees_foutenlijst(pad)
    assert df.values.tolist() == [["V1", "tuinen", "tuinvlak te klein (voor: 3 m² < 5); zie kaart"]]
    leeg = lees_foutenlijst(tmp_path / "nope.csv")
    assert leeg.columns.tolist() == ["vhe_nummer", "stap", "reden"] and len(leeg) == 0
