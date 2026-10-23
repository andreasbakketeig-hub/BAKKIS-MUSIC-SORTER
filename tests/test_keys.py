import pytest

from bakkis_sorter import keys


@pytest.mark.parametrize(
    "raw, code",
    [
        ("8A", "8A"), ("08a", "8A"), ("12B", "12B"), ("Am", "8A"), ("A minor", "8A"), ("F#m", "11A"),
        ("Gbm", "11A"), ("Bb", "6B"), ("C", "8B"), ("C major", "8B"), ("Ab", "4B"), ("Abm", "1A"),
        ("1m", "8A"), ("1d", "8B"), ("6m", "1A"), ("2A or 3A", "2A"), ("8A - 124", "8A"),
        ("", None), (None, None), ("H", None), ("13A", None), ("Deep", None),
    ],
)
def test_to_camelot(raw, code):
    assert keys.to_camelot(raw) == code


def test_compatible_wraps_around_the_wheel():
    assert keys.compatible("8A") == {"8A", "9A", "7A", "8B"}
    assert keys.compatible("12B") == {"12B", "1B", "11B", "12A"}
    assert keys.compatible("1A") == {"1A", "2A", "12A", "1B"}
    assert keys.compatible("x") == set()


def test_every_camelot_code_has_a_name():
    assert len(keys.NAMES) == 24 and keys.NAMES["8A"] == "Am" and keys.NAMES["8B"] == "C"
    assert all(keys.to_camelot(name) == code for code, name in keys.NAMES.items())
