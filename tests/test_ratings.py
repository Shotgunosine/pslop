import pytest

from pmap_calibrate.ratings import Summary, level_5_note, parse_rating, parse_ratings_field, summary


@pytest.mark.parametrize(
    ("levels", "expected"),
    [
        ([5, 5, 5, 5, 5], Summary(5.0, 5)),
        ([5, 5, 5, 5, 3], Summary(4.6, 5)),
        ([5, 5, 5, 3, 3], Summary(4.2, 3)),
        ([5, 5, 5, 3], Summary(4.5, 3)),
        ([3, 3, 3], Summary(3.0, 3)),
        ([5, 5, 5, 5, 1], Summary(4.2, 1)),
    ],
)
def test_summary(levels, expected):
    assert summary(levels) == expected


def test_summary_rejects_invalid_level():
    with pytest.raises(ValueError):
        summary([5, 4])


def test_parse_rating():
    assert parse_rating("Customer Experience=5") == ("Customer Experience", 5)
    assert parse_rating(" Training = 3 ") == ("Training", 3)
    for bad in ["Training", "=5", "Training=4", "Training=O"]:
        with pytest.raises(ValueError):
            parse_rating(bad)


def test_parse_ratings_field():
    assert parse_ratings_field("A=5; B=3;") == [("A", 5), ("B", 3)]


def test_level_5_note():
    assert "at most 1 element(s) at Level 3" in level_5_note(5)
    assert "at most 0 element(s) at Level 3" in level_5_note(4)
