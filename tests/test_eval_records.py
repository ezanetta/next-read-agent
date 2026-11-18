from datetime import UTC, datetime
from pathlib import Path

import pytest

from book_agent.evals.records import EvalRecord, RecordStore, RunConfig, profile_sha256
from tests.library_data import PROFILE

AGENT = RunConfig(system="agent", prompt_id="agent_v2", model="gemini-3.5-flash-lite")


def record(case_id: str = "library-only", run_index: int = 1, **fields: object) -> EvalRecord:
    base: dict[str, object] = {
        "case_set": "recommend_v1",
        "cases_sha256": "c" * 64,
        "profile_sha256": "p" * 64,
        "case_id": case_id,
        "run_index": run_index,
        "config": AGENT,
        "started_at": datetime(2026, 11, 18, 12, 0, tzinfo=UTC),
        "duration_s": 1.5,
    }
    return EvalRecord.model_validate(base | fields)


def test_config_id_is_folder_safe() -> None:
    assert AGENT.id == "agent-agent_v2-gemini-3.5-flash-lite-default"
    odd = RunConfig(system="baseline", prompt_id="baseline_v1", model="a/b c", thinking_level="low")
    assert odd.id == "baseline-baseline_v1-a_b_c-low"


def test_save_exists_and_load_round_trip(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    saved = record(run_index=2)
    path = store.save(saved)

    assert path == tmp_path / "recommend_v1" / AGENT.id / "library-only__run2.json"
    assert store.exists("recommend_v1", AGENT, "library-only", 2)
    assert not store.exists("recommend_v1", AGENT, "library-only", 1)
    assert store.load_all("recommend_v1") == [saved]


def test_load_all_spans_configurations_in_a_stable_order(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    baseline = RunConfig(system="baseline", prompt_id="baseline_v1", model="m")
    store.save(record("b-case", config=AGENT))
    store.save(record("a-case", config=baseline))
    loaded = store.load_all("recommend_v1")
    assert [(r.config.system, r.case_id) for r in loaded] == [
        ("agent", "b-case"),
        ("baseline", "a-case"),
    ]
    assert store.load_all("other_set") == []


def test_unreadable_record_is_an_error_not_a_silent_skip(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    path = store.save(record())
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="is not a valid eval record"):
        store.load_all("recommend_v1")


def test_cases_hashes(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    store.save(record("a"))
    store.save(record("b", cases_sha256="d" * 64))
    assert store.cases_hashes("recommend_v1") == {"c" * 64, "d" * 64}


def test_profile_snapshot_is_kept_per_library(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    sha = store.save_profile("recommend_v1", PROFILE)
    assert sha == profile_sha256(PROFILE)
    snapshot = tmp_path / "recommend_v1" / f"profile_{sha[:12]}.json"
    assert snapshot.exists()
    smaller = PROFILE.model_copy(update={"books": PROFILE.books[:2]})
    other = store.save_profile("recommend_v1", smaller)
    assert other != sha
    assert len(list((tmp_path / "recommend_v1").glob("profile_*.json"))) == 2
