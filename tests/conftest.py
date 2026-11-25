import pytest

from miniwijk import MiniWijk, maak_miniwijk


@pytest.fixture
def miniwijk(tmp_path) -> MiniWijk:
    """Synthetische mini-wijk; zie tests/miniwijk.py voor de indeling."""
    return maak_miniwijk(tmp_path / "miniwijk")
