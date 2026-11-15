import pytest
from pydantic import ValidationError

from book_agent.domain.works import SameWorkAnswer, SameWorkVerdict

SAME, UNSURE, DIFFERENT = SameWorkAnswer.SAME, SameWorkAnswer.UNSURE, SameWorkAnswer.DIFFERENT


@pytest.mark.parametrize(
    ("verdict", "collides"),
    [
        (SameWorkVerdict(answer=SAME, candidate=0), True),
        (SameWorkVerdict(answer=UNSURE, candidate=1), True),  # unsure leans towards same
        (SameWorkVerdict(answer=DIFFERENT), False),
        (SameWorkVerdict(error="timeout"), False),  # a failure is not a collision
    ],
)
def test_collides(verdict: SameWorkVerdict, collides: bool) -> None:
    assert verdict.collides is collides


@pytest.mark.parametrize(
    "fields",
    [
        {},  # neither answer nor error
        {"answer": SAME, "candidate": 0, "error": "both"},
        {"answer": SAME},  # same needs a candidate
        {"answer": UNSURE},
        {"answer": DIFFERENT, "candidate": 0},
        {"answer": SAME, "candidate": -1},
    ],
)
def test_inconsistent_verdicts_are_rejected(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SameWorkVerdict.model_validate(fields)
