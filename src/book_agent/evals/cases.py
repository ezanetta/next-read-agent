"""Versioned eval cases, loaded from evals/cases/<version>.json.

A case set is frozen once it has results: records store the file's hash, and the runner
refuses to add runs to a set whose file changed (D-023). Changes go in a new version.
"""

import hashlib
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, ValidationError, model_validator

from book_agent.domain.recommendations import Filters

CASES_DIR = Path("evals/cases")

_ID = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class RecommendCase(BaseModel, frozen=True):
    """One end-to-end request: free text, explicit flags, or neither."""

    id: str = Field(pattern=_ID)
    request: str | None = None
    flags: Filters = Filters()
    category: str = Field(min_length=1)
    note: str = Field(min_length=1)


class ParserCase(BaseModel, frozen=True):
    """One request with the interpretation it should get."""

    id: str = Field(pattern=_ID)
    request: str = Field(min_length=1)
    has_preference: bool
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    max_pages: int | None = Field(default=None, gt=0)
    min_pages: int | None = Field(default=None, gt=0)  # labeled from request_parser_v2 on
    # Terms that must survive in the preference, ignoring case and accents.
    preference_contains: list[str] = []

    @model_validator(mode="after")
    def _terms_need_a_preference(self) -> Self:
        if self.preference_contains and not self.has_preference:
            raise ValueError(f"{self.id}: preference_contains needs has_preference")
        return self


class _CaseSet[C: RecommendCase | ParserCase](BaseModel, frozen=True):
    version: str = Field(min_length=1)
    description: str = ""
    cases: list[C] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [c.id for c in self.cases]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate case ids: {', '.join(duplicates)}")
        return self


class RecommendCaseSet(_CaseSet[RecommendCase], frozen=True):
    pass


class ParserCaseSet(_CaseSet[ParserCase], frozen=True):
    pass


class LoadedCases[S: RecommendCaseSet | ParserCaseSet](BaseModel, frozen=True):
    """A case set and the hash of the exact file it came from."""

    cases: S
    sha256: str


def load_recommend_cases(
    version: str, cases_dir: Path = CASES_DIR
) -> LoadedCases[RecommendCaseSet]:
    return _load(RecommendCaseSet, version, cases_dir)


def load_parser_cases(version: str, cases_dir: Path = CASES_DIR) -> LoadedCases[ParserCaseSet]:
    return _load(ParserCaseSet, version, cases_dir)


def _load[S: RecommendCaseSet | ParserCaseSet](
    model: type[S], version: str, cases_dir: Path
) -> LoadedCases[S]:
    """Raises ValueError for a missing file, invalid content, or a version mismatch."""
    path = cases_dir / f"{version}.json"
    try:
        content = path.read_bytes()
    except OSError as e:
        raise ValueError(f"case set {version!r} not found at {path}") from e
    try:
        cases = model.model_validate_json(content)
    except ValidationError as e:
        raise ValueError(f"{path} is not a valid case set: {e.error_count()} errors") from e
    if cases.version != version:
        raise ValueError(f"{path} declares version {cases.version!r}, expected {version!r}")
    return LoadedCases[S](cases=cases, sha256=hashlib.sha256(content).hexdigest())
