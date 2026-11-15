"""Measures the LLM same-work check on a small labeled set of real pairs.

The measurement is marked live and excluded by default; the fixture check always runs.

Run with: uv run pytest -m live tests/test_same_work_live.py -s
Uses the LLM configured in .env: one call per pair (11 calls), run one at a time.

The assertion is asymmetric, like the cost of the two errors: a same-work pair judged
DIFFERENT would recommend a book already read, so none is allowed; a different pair
judged SAME or UNSURE only loses one recommendation, so one is tolerated.
"""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from book_agent.config import get_settings
from book_agent.domain.works import SameWorkVerdict, WorkRef
from book_agent.same_work_judge import LLMSameWorkJudge
from book_agent.wiring import build_llm

PAIRS_FILE = Path(__file__).parent / "fixtures" / "same_work_pairs.json"
MAX_FALSE_SAME = 1


class LabeledPair(BaseModel, frozen=True):
    id: str
    note: str | None = None
    book: WorkRef
    library: list[WorkRef]
    same_as: int | None  # index of the library book it is, None if it is none of them


def load_pairs() -> list[LabeledPair]:
    return TypeAdapter(list[LabeledPair]).validate_python(json.loads(PAIRS_FILE.read_text()))


def test_labeled_pairs_are_well_formed() -> None:
    pairs = load_pairs()
    assert len({p.id for p in pairs}) == len(pairs)
    for pair in pairs:
        assert pair.same_as is None or 0 <= pair.same_as < len(pair.library), pair.id
    assert {"kafka-translation", "little-prince-translation", "steinbeck-different"} <= {
        p.id for p in pairs
    }


def _outcome(pair: LabeledPair, verdict: SameWorkVerdict) -> str:
    if verdict.error is not None:
        return "ERROR"
    if pair.same_as is None:
        return "false same" if verdict.collides else "ok"
    if not verdict.collides:
        return "FALSE DIFFERENT"
    return "ok" if verdict.candidate == pair.same_as else "WRONG CANDIDATE"


@pytest.mark.live
async def test_judge_on_labeled_pairs() -> None:
    try:
        llm = build_llm(get_settings())
    except ValidationError:
        pytest.skip("the LLM is not configured in .env")
    judge = LLMSameWorkJudge(llm)

    rows: list[tuple[LabeledPair, SameWorkVerdict, str]] = []
    for pair in load_pairs():  # sequential: stays well under per-minute limits
        verdict = await judge.judge(pair.book, pair.library)
        rows.append((pair, verdict, _outcome(pair, verdict)))

    print()
    for pair, verdict, outcome in rows:
        answer = verdict.answer.value if verdict.answer else f"error: {verdict.error}"
        candidate = "" if verdict.candidate is None else f" #{verdict.candidate}"
        print(f"{pair.id:42} expected {pair.same_as!s:5} got {answer}{candidate:4} {outcome}")

    by_outcome = [outcome for _, _, outcome in rows]
    assert by_outcome.count("ERROR") == 0, "the judge failed on some pairs"
    assert by_outcome.count("FALSE DIFFERENT") == 0, "a same-work pair was judged different"
    assert by_outcome.count("WRONG CANDIDATE") == 0, "a collision named the wrong library book"
    assert by_outcome.count("false same") <= MAX_FALSE_SAME
