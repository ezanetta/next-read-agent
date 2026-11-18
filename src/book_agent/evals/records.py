"""Eval run records: one complete JSON file per run, the only thing reports read.

Running and scoring are separate (D-021): a record holds everything a run produced, so
reports, a new acceptance policy or the phase-2 judge never need new LLM calls.
Records live in data/evals/ (gitignored): they contain the reader's library (D-024).

Layout:
    data/evals/<case set>/<configuration id>/<case id>__run<k>.json
    data/evals/<case set>/profile_<hash>.json    the library the runs used
"""

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from book_agent.agent import TurnTrace
from book_agent.domain.models import Profile
from book_agent.domain.recommendations import RecommendationList, ValidationResult
from book_agent.domain.requests import ResolvedRequest
from book_agent.files import write_atomic
from book_agent.llm.calls import LLMCall

SCHEMA_VERSION = 1
RESULTS_DIR = Path("data/evals")

System = Literal["baseline", "agent"]

_UNSAFE = re.compile(r"[^a-zA-Z0-9._-]+")


class RunConfig(BaseModel, frozen=True):
    """What is being compared. Git commit is recorded per run but is not part of it:
    changes outside the prompt version start a fresh results directory (D-021)."""

    system: System
    prompt_id: str
    model: str
    thinking_level: str | None = None

    @property
    def id(self) -> str:
        """A folder-safe name, e.g. "agent-agent_v2-gemini-3.5-flash-lite-default"."""
        parts = [self.system, self.prompt_id, self.model, self.thinking_level or "default"]
        return "-".join(_UNSAFE.sub("_", p) for p in parts)


class EvalRecord(BaseModel, frozen=True):
    """Everything one run produced. Acceptance is not stored: it is decided at report
    time with the current policy, from final_validation."""

    schema_version: int = SCHEMA_VERSION
    # Identity
    case_set: str
    cases_sha256: str
    profile_sha256: str
    case_id: str
    run_index: int = Field(ge=1)
    config: RunConfig
    feedback_prompt_id: str | None = None  # agent only
    request_prompt_id: str | None = None  # set when a request text was interpreted
    git_commit: str | None = None
    git_dirty: bool = False
    started_at: datetime
    duration_s: float = Field(ge=0)
    # What happened
    request: ResolvedRequest | None = None  # interpreted request and merged filters
    error_kind: str | None = None  # "interpretation", "unparseable", "llm_error", ...
    error: str | None = None
    recommendations: RecommendationList | None = None  # the last parsed submission
    raw_text: str | None = None  # baseline reply, or the interpreter's unparseable reply
    final_validation: ValidationResult | None = None  # accepted, or the last one on failure
    rejections: list[ValidationResult] = []  # agent: each rejected submission, in order
    turns: list[TurnTrace] = []  # agent trace
    calls: list[LLMCall] = []  # every LLM call, in order
    iterations: int = 0  # agent turns; 0 for the baseline
    submissions: int = 0  # validated submissions (the baseline's one reply counts as one)
    repair_turn_used: bool = False


def profile_sha256(profile: Profile) -> str:
    return hashlib.sha256(profile.model_dump_json().encode("utf-8")).hexdigest()


class RecordStore:
    """Reads and writes records and profile snapshots under a results directory."""

    def __init__(self, root: Path = RESULTS_DIR) -> None:
        self.root = root

    def path(self, case_set: str, config: RunConfig, case_id: str, run_index: int) -> Path:
        return self.root / case_set / config.id / f"{case_id}__run{run_index}.json"

    def exists(self, case_set: str, config: RunConfig, case_id: str, run_index: int) -> bool:
        return self.path(case_set, config, case_id, run_index).exists()

    def save(self, record: EvalRecord) -> Path:
        path = self.path(record.case_set, record.config, record.case_id, record.run_index)
        write_atomic(path, record.model_dump_json(indent=1) + "\n")
        return path

    def load_all(self, case_set: str) -> list[EvalRecord]:
        """Every record of a case set, across configurations, in a stable order.

        Raises ValueError naming the first unreadable file, rather than skipping it.
        """
        records: list[EvalRecord] = []
        for path in sorted((self.root / case_set).glob("*/*__run*.json")):
            try:
                records.append(EvalRecord.model_validate_json(path.read_bytes()))
            except ValidationError as e:
                raise ValueError(
                    f"{path} is not a valid eval record: {e.error_count()} errors"
                ) from e
        return records

    def cases_hashes(self, case_set: str) -> set[str]:
        """The case-file hashes existing records were produced with."""
        return {r.cases_sha256 for r in self.load_all(case_set)}

    def save_profile(self, case_set: str, profile: Profile) -> str:
        """Keep a snapshot of the library the runs used, for the phase-2 judge.

        One file per distinct library, named by its hash, so a changed export never
        overwrites the snapshot older records point to.
        """
        sha = profile_sha256(profile)
        path = self.root / case_set / f"profile_{sha[:12]}.json"
        if not path.exists():
            write_atomic(path, profile.model_dump_json(indent=1) + "\n")
        return sha
