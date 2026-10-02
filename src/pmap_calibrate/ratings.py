"""HHS three-level rating rules: element levels, summary scores, and level bands."""

from dataclasses import dataclass
from fractions import Fraction

VALID_LEVELS = (1, 3, 5)
LEVEL_NAMES = {5: "Outstanding", 3: "Fully Successful", 1: "Unsuccessful"}
LEVEL_5_MIN = Fraction("4.60")
LEVEL_3_MIN = Fraction("3.00")
NARRATIVE_LIMIT = 6000
NEAR_LIMIT_FRACTION = 0.98


@dataclass(frozen=True)
class Summary:
    score: float
    level: int

    def describe(self) -> str:
        return f"{self.score:.2f} (Level {self.level}, {LEVEL_NAMES[self.level]})"


def level_for_score(score: Fraction) -> int:
    if score >= LEVEL_5_MIN:
        return 5
    if score >= LEVEL_3_MIN:
        return 3
    return 1


def summary(levels: list[int]) -> Summary:
    """Unweighted mean of element levels; any (critical) element at Level 1 makes the summary Level 1."""
    if not levels:
        raise ValueError("At least one element rating is needed to compute a summary.")
    for level in levels:
        validate_level(level)
    score = Fraction(sum(levels), len(levels))
    level = 1 if 1 in levels else level_for_score(score)
    return Summary(score=round(float(score), 2), level=level)


def validate_level(level: int) -> int:
    if level not in VALID_LEVELS:
        raise ValueError(f"Level must be 1, 3, or 5 (got {level}).")
    return level


def parse_rating(text: str) -> tuple[str, int]:
    """Parse "Element name=5" into ("Element name", 5)."""
    name, sep, level = text.rpartition("=")
    name = name.strip()
    if not sep or not name:
        raise ValueError(f"Expected ELEMENT=LEVEL, e.g. 'Customer Experience=5' (got {text!r}).")
    try:
        value = int(level.strip())
    except ValueError:
        raise ValueError(f"Level in {text!r} must be 1, 3, or 5.") from None
    return name, validate_level(value)


def parse_ratings_field(text: str) -> list[tuple[str, int]]:
    """Parse a manifest field like "Customer Experience=5; Training=3"."""
    return [parse_rating(part) for part in text.split(";") if part.strip()]


def level_5_note(element_count: int) -> str:
    """Explain how many Level 3 elements a Level 5 summary can absorb."""
    max_threes = max(
        (k for k in range(element_count + 1)
         if Fraction(5 * (element_count - k) + 3 * k, element_count) >= LEVEL_5_MIN),
        default=0,
    )
    return (
        f"With {element_count} elements, a Level 5 summary allows at most "
        f"{max_threes} element(s) at Level 3 and none at Level 1."
    )
