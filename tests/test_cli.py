import pytest

from tuinscan.cli import main


def test_cli_help_lists_steps(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for stap in ["download", "tuinen", "score", "rapport", "terugkoppeling", "inspecteer"]:
        assert stap in out
