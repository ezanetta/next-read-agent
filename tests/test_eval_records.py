import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from book_agent.evals.records import (
    HARNESS_VERSION,
    EvalRecord,
    RecordStore,
    RunConfig,
    profile_sha256,
)
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


def test_harness_version_separates_folders_and_round_1_keeps_its_name() -> None:
    round_1 = RunConfig(system="agent", prompt_id="agent_v3", model="m")
    round_2 = round_1.model_copy(update={"harness": 2})
    assert round_1.harness == 1  # records from before the field load as round 1
    assert round_1.id == "agent-agent_v3-m-default"
    assert round_2.id == "agent-agent_v3-m-default-h2"
    assert round_1 != round_2  # different configurations in the report


def test_the_current_harness_version_is_not_round_1() -> None:
    assert HARNESS_VERSION >= 2


def test_a_round_1_record_without_the_field_loads_as_harness_1(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    path = store.save(record())
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["config"]["harness"]  # as written before the field existed
    path.write_text(json.dumps(data), encoding="utf-8")
    [loaded] = store.load_all("recommend_v1")
    assert loaded.config.harness == 1 and path.parent.name == loaded.config.id


def test_the_profile_hash_ignores_ratings_implied_while_false() -> None:
    from book_agent.domain.models import Profile
    from book_agent.evals.records import profile_sha256
    from tests.library_data import PROFILE as LIBRARY

    old_json = Profile.model_dump_json(LIBRARY, exclude={"ratings_implied"})
    assert profile_sha256(LIBRARY) == hashlib.sha256(old_json.encode("utf-8")).hexdigest()
    implied = LIBRARY.model_copy(update={"ratings_implied": True})
    assert profile_sha256(implied) != profile_sha256(LIBRARY)
