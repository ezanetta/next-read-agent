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

HARNESS_VERSION = 3
"""Part of the configuration identity (D-021): bump it whenever the agent loop, the
validator, the tools or an adapter change in a way that affects results, so new runs go
to a new folder instead of mixing with runs of the old code. Prompt changes don't need
it: they have their own versions.

1: round 1 (F-011), up to commit 3f950f7.
2: Google Books retries (D-027), forced submit and the notice at 2 turns left (D-028),
   " - " subtitles and combined volumes in title matching (D-029). Round 2 (F-013), up
   to commit 52f19aa.
3: one retry on LLM server errors (D-030), interleaved run order (D-031)."""

System = Literal["baseline", "agent"]

_UNSAFE = re.compile(r"[^a-zA-Z0-9._-]+")


class RunConfig(BaseModel, frozen=True):
    """What is being compared: system, prompt version, model, thinking level and harness
    version. Git commit is recorded per run but is not part of it (D-021)."""

    system: System
    prompt_id: str
    model: str
    thinking_level: str | None = None
    harness: int = Field(default=1, ge=1)  # records from before the field are round 1

    @property
    def id(self) -> str:
        """A folder-safe name, e.g. "agent-agent_v3-gemini-3.5-flash-lite-default-h2".

        Round-1 records (harness 1) keep their original name, without the suffix, so
        their folders stay where they are.
        """
        parts = [self.system, self.prompt_id, self.model, self.thinking_level or "default"]
        name = "-".join(_UNSAFE.sub("_", p) for p in parts)
        return name if self.harness == 1 else f"{name}-h{self.harness}"


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
    # Library retrieval (D-041): titles shown to the agent, most related first, and the
    # error when retrieval was attempted and failed. Empty for prompts without retrieval.
    retrieved: list[str] = []
    retrieval_error: str | None = None


def profile_sha256(profile: Profile) -> str:
    """The hash that identifies a library in records. `ratings_implied` is left out while
    False, so profiles from before that field keep their hash (D-043)."""
    exclude = None if profile.ratings_implied else {"ratings_implied"}
    return hashlib.sha256(profile.model_dump_json(exclude=exclude).encode("utf-8")).hexdigest()


class RecordStore:
    """Reads and writes records and profile snapshots under a results directory."""

    def __init__(self, root: Path = RESULTS_DIR) -> None:
        self.root = root

    def path(self, case_set: str, config: RunConfig, case_id: str, run_index: int) -> Path:
        return self.root / case_set / config.id / f"{case_id}__run{run_index}.json"

    def exists(self, case_set: str, config: RunConfig, case_id: str, run_index: int) -> bool:
        return self.path(case_set, config, case_id, run_index).exists()

    def load(
        self, case_set: str, config: RunConfig, case_id: str, run_index: int
    ) -> EvalRecord | None:
        path = self.path(case_set, config, case_id, run_index)
        return EvalRecord.model_validate_json(path.read_bytes()) if path.exists() else None

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
        path = self._profile_path(case_set, sha)
        if not path.exists():
            write_atomic(path, profile.model_dump_json(indent=1) + "\n")
        return sha

    def load_profile(self, case_set: str, sha: str) -> Profile:
        """The library snapshot a record points to (its profile_sha256)."""
        return Profile.model_validate_json(self._profile_path(case_set, sha).read_bytes())

    def _profile_path(self, case_set: str, sha: str) -> Path:
        return self.root / case_set / f"profile_{sha[:12]}.json"
