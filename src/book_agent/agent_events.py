"""Progress events from a run, for callers that show it as it happens (D-043).

The agent loop and the pipeline call an optional sink with these; the CLI and evals pass
none. Each event carries what the loop already builds (the turn trace, the validation),
so streaming adds no second account of what happened.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from book_agent.domain.recommendations import ValidationResult
from book_agent.domain.requests import ResolvedRequest

if TYPE_CHECKING:  # agent.py imports this module; the trace type is only an annotation
    from book_agent.agent import TurnTrace


@dataclass(frozen=True)
class RequestInterpreted:
    request: ResolvedRequest


@dataclass(frozen=True)
class SubmissionChecking:
    """A submission parsed and is about to be checked (existence, library, filters)."""

    iteration: int
    titles: list[str]


@dataclass(frozen=True)
class SubmissionChecked:
    iteration: int
    validation: ValidationResult
    accepted: bool


@dataclass(frozen=True)
class TurnFinished:
    """One completed turn: its tool calls and outcomes (searches included)."""

    trace: "TurnTrace"


AgentEvent = RequestInterpreted | SubmissionChecking | SubmissionChecked | TurnFinished
EventSink = Callable[[AgentEvent], None]
