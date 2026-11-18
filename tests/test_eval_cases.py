import json
from pathlib import Path

import pytest

from book_agent.domain.recommendations import Filters
from book_agent.evals.cases import load_parser_cases, load_recommend_cases


def test_recommend_v1_loads_with_13_unique_cases() -> None:
    loaded = load_recommend_cases("recommend_v1")
    cases = loaded.cases.cases
    assert len(cases) == 13
    by_id = {c.id: c for c in cases}
    assert by_id["kafka-filters"].flags == Filters(language="en", max_pages=200)
    assert by_id["library-only"].request is None
    assert len(loaded.sha256) == 64


def test_request_parser_v1_loads() -> None:
    cases = load_parser_cases("request_parser_v1").cases.cases
    assert len(cases) == 23
    two_languages = next(c for c in cases if c.id == "two-languages")
    assert two_languages.language is None
    assert two_languages.preference_contains == ["thriller", "inglés", "español"]


def write(tmp_path: Path, version: str, content: object) -> Path:
    path = tmp_path / f"{version}.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def case(case_id: str) -> dict[str, object]:
    return {"id": case_id, "category": "c", "note": "n"}


def test_hash_changes_with_the_file(tmp_path: Path) -> None:
    write(tmp_path, "v", {"version": "v", "cases": [case("a")]})
    first = load_recommend_cases("v", tmp_path).sha256
    write(tmp_path, "v", {"version": "v", "cases": [case("a"), case("b")]})
    assert load_recommend_cases("v", tmp_path).sha256 != first


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ({"version": "v", "cases": [case("a"), case("a")]}, "not a valid case set"),
        ({"version": "other", "cases": [case("a")]}, "declares version 'other'"),
        ({"version": "v", "cases": [case("Bad Id")]}, "not a valid case set"),
        ({"version": "v", "cases": []}, "not a valid case set"),
    ],
)
def test_invalid_case_sets_are_rejected(tmp_path: Path, content: object, reason: str) -> None:
    write(tmp_path, "v", content)
    with pytest.raises(ValueError, match=reason):
        load_recommend_cases("v", tmp_path)


def test_missing_case_set_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="case set 'nope' not found"):
        load_recommend_cases("nope", tmp_path)


def test_preference_terms_need_a_preference(tmp_path: Path) -> None:
    bad = {"id": "x", "request": "en inglés", "has_preference": False, "preference_contains": ["a"]}
    write(tmp_path, "p", {"version": "p", "cases": [bad]})
    with pytest.raises(ValueError, match="not a valid case set"):
        load_parser_cases("p", tmp_path)
