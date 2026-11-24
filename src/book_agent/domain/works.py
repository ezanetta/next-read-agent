"""Works versus editions: is a book the same work as one already in the reader's library?

The same work means the same text in any edition, translation or title
("La metamorfosis" and "The Metamorphosis"). Deciding it needs outside knowledge, so the
validator asks a SameWorkJudge (see ports.py); these are the types they exchange.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator


class WorkRef(BaseModel, frozen=True):
    title: str = Field(min_length=1)
    author: str


class SameWorkAnswer(StrEnum):
    SAME = "same"
    UNSURE = "unsure"
    DIFFERENT = "different"


class SameWorkVerdict(BaseModel, frozen=True):
    """The judge's answer, or the reason it could not give one.

    `candidate` is the index of the library book the work most likely is: required for
    SAME and UNSURE, absent for DIFFERENT. A failed check has an `error` and no answer.
    """

    answer: SameWorkAnswer | None = None
    candidate: int | None = Field(default=None, ge=0)
    error: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.answer is None) == (self.error is None):
            raise ValueError("a verdict has either an answer or an error")
        if self.answer is SameWorkAnswer.DIFFERENT and self.candidate is not None:
            raise ValueError("a DIFFERENT verdict names no candidate")
        if self.collides and self.candidate is None:
            raise ValueError(f"a {self.answer} verdict needs a candidate")
        return self

    @property
    def collides(self) -> bool:
        """SAME or UNSURE: when unsure, the book counts as already in the library.

        A false "same" loses one good recommendation, which a retry can replace; a false
        "different" recommends a book the reader already has, the failure being fixed.
        """
        return self.answer in (SameWorkAnswer.SAME, SameWorkAnswer.UNSURE)


class WorkInfo(BaseModel, frozen=True):
    """What a catalog says a work is about: used to describe the reader's own books (D-041)."""

    description: str | None = None  # plain text
    subjects: tuple[str, ...] = ()
