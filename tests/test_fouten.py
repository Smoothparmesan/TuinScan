import csv

from tuinscan.fouten import Foutenlijst


def test_foutenlijst_csv(tmp_path):
    f = Foutenlijst()
    f.voeg_toe("V1", "koppeling", "geen BAG-id")
    f.voeg_toe("V2", "tuinen", "geen perceel")
    pad = tmp_path / "foutenlijst.csv"
    f.schrijf(pad)
    rijen = list(csv.reader(pad.open(encoding="utf-8", newline="")))
    assert rijen[0] == ["vhe_nummer", "stap", "reden"]
    assert len(rijen) == 3
    assert f.aantal == 2
    assert f.vhe_nummers() == {"V1", "V2"}
