"""The phase-2 judge: grading each recommended book of stored eval records (D-033).

Judgments are a layer on top of the records, never written into them (D-021): one file
per record and judge configuration, holding one judgment per book. Only books that
matched a catalog edition are judged; the rest already fail the hard rules.

Layout:
    data/evals/<case set>/judgments/<judge id>/<record config id>/<case>__run<k>.json
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import perf_counter

from pydantic import BaseModel, Field

from book_agent.domain.models import Book, Profile
from book_agent.domain.recommendations import Filters
from book_agent.evals.pacing import QuotaExhausted
from book_agent.evals.records import EvalRecord, RecordStore
from book_agent.files import write_atomic
from book_agent.json_reply import ReplyParseError, extract_json, parse_model
from book_agent.llm.base import LLMClient, LLMError, Message
from book_agent.llm.calls import LLMCall
from book_agent.prompts import judge_v1
from book_agent.prompts.judge_v1 import CitedBook, JudgeInput

CALL_NAME = "judge"
DEFAULT_JUDGE_MODEL = "gemini-3.5-flash-lite"
MAX_TOKENS = 1024

_UNSAFE = re.compile(r"[^a-zA-Z0-9._-]+")


@dataclass(frozen=True)
class JudgePrompt:
    prompt_id: str
    system: str
    build_user_prompt: Callable[[JudgeInput], str]


JUDGE_PROMPTS = {
    judge_v1.PROMPT_ID: JudgePrompt(
        judge_v1.PROMPT_ID, judge_v1.SYSTEM, judge_v1.build_user_prompt
    ),
}
DEFAULT_JUDGE_PROMPT = JUDGE_PROMPTS[judge_v1.PROMPT_ID]


class Verdict(StrEnum):
    NO = "no"
    PARTIAL = "partial"
    YES = "yes"

    @property
    def rank(self) -> int:
        """Order for comparing verdicts: no < partial < yes."""
        return _RANKS[self]


_RANKS = {Verdict.NO: 0, Verdict.PARTIAL: 1, Verdict.YES: 2}


class Criterion(StrEnum):
    FITS_REQUEST = "fits_request"
    REASON_QUALITY = "reason_quality"


class CriterionJudgment(BaseModel, frozen=True):
    justification: str = Field(min_length=1)
    verdict: Verdict


class JudgeConfig(BaseModel, frozen=True):
    """What identifies a judge: prompt version, model and thinking level."""

    prompt_id: str
    model: str
    thinking_level: str | None = None

    @property
    def id(self) -> str:
        parts = [self.prompt_id, self.model, self.thinking_level or "default"]
        return "-".join(_UNSAFE.sub("_", p) for p in parts)


class BookRef(BaseModel, frozen=True):
    """One recommended book of one stored run."""

    config_id: str
    case_id: str
    run_index: int
    item_index: int  # position in the run's final submission


class BookJudgment(BaseModel, frozen=True):
    item_index: int
    title: str
    author: str
    has_request: bool
    had_description: bool
    fits_request: CriterionJudgment | None = None  # None without request text, or on error
    reason_quality: CriterionJudgment | None = None
    error: str | None = None  # not judged: LLM failure or unreadable reply
    raw_text: str | None = None  # kept when the reply was unreadable
    call: LLMCall | None = None

    @property
    def judged(self) -> bool:
        return self.error is None

    def verdict(self, criterion: Criterion) -> Verdict | None:
        judgment = self.fits_request if criterion is Criterion.FITS_REQUEST else self.reason_quality
        return judgment.verdict if judgment else None


class RecordJudgments(BaseModel):
    """The judgments of one record's books by one judge configuration."""

    case_set: str
    config_id: str  # the record's configuration
    case_id: str
    run_index: int
    judge: JudgeConfig
    git_commit: str | None = None
    judged_at: datetime
    judgments: list[BookJudgment] = []

    def get(self, item_index: int) -> BookJudgment | None:
        return next((j for j in self.judgments if j.item_index == item_index), None)


class _CriterionReply(BaseModel):
    justification: str = Field(min_length=1)
    verdict: Verdict


class _Reply(BaseModel):
    fits_request: _CriterionReply | None = None
    reason_quality: _CriterionReply


# --- Inputs ---------------------------------------------------------------------------


def ref_of(record: EvalRecord, item_index: int) -> BookRef:
    return BookRef(
        config_id=record.config.id,
        case_id=record.case_id,
        run_index=record.run_index,
        item_index=item_index,
    )


def judgeable_items(record: EvalRecord) -> list[int]:
    """Indexes of the final submission's books that matched a catalog edition."""
    if record.final_validation is None:
        return []
    return [i for i, item in enumerate(record.final_validation.items) if item.match is not None]


def judge_input(record: EvalRecord, item_index: int, profile: Profile) -> JudgeInput:
    """What the judge (and the labeler) sees about one book. Raises ValueError if the
    book has no catalog match."""
    if record.final_validation is None:
        raise ValueError(f"{record.case_id} run {record.run_index} has no final submission")
    item = record.final_validation.items[item_index]
    if item.match is None:
        raise ValueError(f"{item.recommendation.title!r} has no catalog match to judge")
    request = record.request
    return JudgeInput(
        request=request.text if request else None,
        preference=request.preference if request else None,
        filters=request.filters if request else Filters(),
        title=item.match.title,
        author=", ".join(item.match.authors) or item.recommendation.author,
        year=item.match.year,
        pages=item.match.pages,
        categories=item.match.categories,
        description=item.match.description,
        reason=item.recommendation.reason,
        cited=[_cited(t, profile) for t in item.cited_titles],
    )


def _cited(title: str, profile: Profile) -> CitedBook:
    book: Book | None = next((b for b in profile.books if b.title == title), None)
    if book is None:  # cited_titles come from the same profile, so this shouldn't happen
        return CitedBook(title=title, author="unknown", rating=None, shelf="unknown")
    return CitedBook(
        title=book.title, author=book.author, rating=book.my_rating, shelf=book.status.value
    )


# --- Calling the judge ----------------------------------------------------------------


def parse_judgment(text: str, has_request: bool) -> _Reply:
    """Read the judge's reply. fits_request must be present exactly when there is a
    request. Raises ReplyParseError with the reason."""
    reply = parse_model(_Reply, extract_json(text, strict=False))
    if has_request and reply.fits_request is None:
        raise ReplyParseError("fits_request is missing")
    if not has_request and reply.fits_request is not None:
        raise ReplyParseError("fits_request given without a request")
    return reply


async def judge_book(
    item: JudgeInput, item_index: int, llm: LLMClient, prompt: JudgePrompt = DEFAULT_JUDGE_PROMPT
) -> BookJudgment:
    """One judge call. LLM failures and unreadable replies become a judgment with an
    error; QuotaExhausted is not an LLMError, so it propagates and stops the run."""
    base = {
        "item_index": item_index,
        "title": item.title,
        "author": item.author,
        "has_request": item.has_request,
        "had_description": bool(item.description),
    }
    started = perf_counter()
    try:
        response = await llm.complete(
            system=prompt.system,
            messages=[Message.user(prompt.build_user_prompt(item))],
            max_tokens=MAX_TOKENS,
            temperature=0.0,  # the same book should get the same grade every time
        )
    except LLMError as e:
        failed = LLMCall.failed(CALL_NAME, e, started)
        return BookJudgment.model_validate(
            {**base, "error": f"LLM call failed: {e}", "call": failed}
        )
    call = LLMCall.succeeded(CALL_NAME, response, started)
    text = response.message.text
    try:
        reply = parse_judgment(text, item.has_request)
    except ReplyParseError as e:
        return BookJudgment.model_validate(
            {**base, "error": f"unreadable reply: {e}", "raw_text": text, "call": call}
        )
    return BookJudgment.model_validate(
        {
            **base,
            "fits_request": reply.fits_request.model_dump() if reply.fits_request else None,
            "reason_quality": reply.reason_quality.model_dump(),
            "call": call,
        }
    )


# --- Storage --------------------------------------------------------------------------


class JudgmentStore:
    def __init__(self, records: RecordStore) -> None:
        self.records = records

    def folder(self, case_set: str, judge: JudgeConfig | str) -> Path:
        """A judge's folder; `judge` is a configuration or its id (as the report gets it)."""
        judge_id = judge if isinstance(judge, str) else judge.id
        return self.records.root / case_set / "judgments" / judge_id

    def path(
        self, case_set: str, judge: JudgeConfig | str, config_id: str, case_id: str, run: int
    ) -> Path:
        return self.folder(case_set, judge) / config_id / f"{case_id}__run{run}.json"

    def load(
        self, case_set: str, judge: JudgeConfig | str, config_id: str, case_id: str, run: int
    ) -> RecordJudgments | None:
        path = self.path(case_set, judge, config_id, case_id, run)
        return RecordJudgments.model_validate_json(path.read_bytes()) if path.exists() else None

    def save(self, judgments: RecordJudgments) -> None:
        path = self.path(
            judgments.case_set,
            judgments.judge,
            judgments.config_id,
            judgments.case_id,
            judgments.run_index,
        )
        judgments.judgments.sort(key=lambda j: j.item_index)
        write_atomic(path, judgments.model_dump_json(indent=1) + "\n")

    def load_all(self, case_set: str, judge: JudgeConfig | str) -> list[RecordJudgments]:
        folder = self.folder(case_set, judge)
        return [
            RecordJudgments.model_validate_json(p.read_bytes())
            for p in sorted(folder.glob("*/*__run*.json"))
        ]

    def judged(self, case_set: str, judge: JudgeConfig | str) -> dict[BookRef, BookJudgment]:
        """Every stored judgment of this judge, by book. Errors are included."""
        return {
            BookRef(
                config_id=r.config_id,
                case_id=r.case_id,
                run_index=r.run_index,
                item_index=j.item_index,
            ): j
            for r in self.load_all(case_set, judge)
            for j in r.judgments
        }


# --- Running --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedBook:
    record: EvalRecord
    item_index: int

    @property
    def ref(self) -> BookRef:
        return ref_of(self.record, self.item_index)


@dataclass
class JudgeSummary:
    judged: int = 0
    errors: int = 0
    stopped: str | None = None


def plan_books(
    records: Iterable[EvalRecord],
    store: JudgmentStore,
    case_set: str,
    judge: JudgeConfig,
    only: set[BookRef] | None = None,
) -> list[PlannedBook]:
    """Matched books without a successful judgment yet (errors are retried), optionally
    limited to `only` (the calibration sample)."""
    done = {ref for ref, j in store.judged(case_set, judge).items() if j.judged}
    planned: list[PlannedBook] = []
    for record in records:
        for index in judgeable_items(record):
            ref = ref_of(record, index)
            if ref in done or (only is not None and ref not in only):
                continue
            planned.append(PlannedBook(record=record, item_index=index))
    return planned


async def run_judge(
    plan: list[PlannedBook],
    *,
    case_set: str,
    judge: JudgeConfig,
    profiles: Callable[[str], Profile],
    llm: LLMClient,
    store: JudgmentStore,
    git_commit: str | None,
    progress: Callable[[int, int, PlannedBook], None] | None = None,
) -> JudgeSummary:
    """Judge each planned book and save its record's file after each. A quota stop ends
    the loop: finished judgments stay, and a rerun resumes."""
    prompt = JUDGE_PROMPTS.get(judge.prompt_id)
    if prompt is None:
        raise ValueError(f"unknown judge prompt {judge.prompt_id!r}")
    summary = JudgeSummary()
    for number, planned in enumerate(plan, start=1):
        if progress is not None:
            progress(number, len(plan), planned)
        record = planned.record
        item = judge_input(record, planned.item_index, profiles(record.profile_sha256))
        try:
            judgment = await judge_book(item, planned.item_index, llm, prompt)
        except QuotaExhausted as e:
            summary.stopped = str(e)
            break
        existing = store.load(case_set, judge, record.config.id, record.case_id, record.run_index)
        file = existing or RecordJudgments(
            case_set=case_set,
            config_id=record.config.id,
            case_id=record.case_id,
            run_index=record.run_index,
            judge=judge,
            git_commit=git_commit,
            judged_at=datetime.now(UTC),
        )
        file.judgments = [j for j in file.judgments if j.item_index != planned.item_index]
        file.judgments.append(judgment)
        store.save(file)
        summary.judged += 1
        summary.errors += not judgment.judged
    return summary
