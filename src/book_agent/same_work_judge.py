"""The LLM same-work check: implements the SameWorkJudge port with one short call.

Leans towards "same work" when unsure, in code as well as in the prompt: UNSURE counts as
a collision (SameWorkVerdict.collides), and DIFFERENT is only accepted from a reply that
parses cleanly. judge() never raises; failures come back as a verdict with an error.
"""

from collections.abc import Sequence
from time import perf_counter
from typing import Literal

from pydantic import BaseModel

from book_agent.domain.works import SameWorkAnswer, SameWorkVerdict, WorkRef
from book_agent.json_reply import ReplyParseError, extract_json, parse_model
from book_agent.llm.base import LLMClient, LLMError, Message
from book_agent.llm.calls import LLMCall
from book_agent.prompts import same_work_v1

CALL_NAME = "judge_same_work"
DEFAULT_MAX_TOKENS = 512  # room for a little thinking on top of a tiny JSON answer


class _Reply(BaseModel):
    verdict: Literal["same", "different", "unsure"]
    candidate: int | None = None  # 1-based, as numbered in the prompt


class LLMSameWorkJudge:
    """SameWorkJudge backed by an LLMClient.

    Keeps a record of every call in `calls`, so the run that owns the judge can report
    its tokens and latency alongside its own calls.
    """

    def __init__(self, llm: LLMClient, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        self._llm = llm
        self._max_tokens = max_tokens
        self.calls: list[LLMCall] = []

    async def judge(self, book: WorkRef, candidates: Sequence[WorkRef]) -> SameWorkVerdict:
        if not candidates:  # nothing it could be: no call needed
            return SameWorkVerdict(answer=SameWorkAnswer.DIFFERENT)

        started = perf_counter()
        try:
            response = await self._llm.complete(
                system=same_work_v1.SYSTEM,
                messages=[Message.user(same_work_v1.build_user_prompt(book, candidates))],
                max_tokens=self._max_tokens,
                temperature=0.0,  # the same pair should get the same answer every time
            )
        except LLMError as e:
            self.calls.append(LLMCall.failed(CALL_NAME, e, started))
            return SameWorkVerdict(error=f"LLM call failed: {e}")

        self.calls.append(LLMCall.succeeded(CALL_NAME, response, started))
        try:
            return parse_verdict(response.message.text, len(candidates))
        except ReplyParseError as e:
            return SameWorkVerdict(error=f"unreadable reply: {e}")


def parse_verdict(text: str, candidate_count: int) -> SameWorkVerdict:
    """Read the judge's JSON reply. Raises ReplyParseError with a specific reason.

    The candidate number is converted to a 0-based index. SAME or UNSURE without a
    candidate is accepted only when there is a single candidate; a DIFFERENT that names
    a candidate contradicts itself and is rejected rather than trusted.
    """
    reply = parse_model(_Reply, extract_json(text))
    answer = SameWorkAnswer(reply.verdict)
    if answer is SameWorkAnswer.DIFFERENT:
        if reply.candidate is not None:
            raise ReplyParseError(f"'different' names candidate {reply.candidate}")
        return SameWorkVerdict(answer=answer)

    number = reply.candidate
    if number is None and candidate_count == 1:
        number = 1
    if number is None:
        raise ReplyParseError(f"{answer.value!r} names no candidate")
    if not 1 <= number <= candidate_count:
        raise ReplyParseError(f"candidate {number} is not between 1 and {candidate_count}")
    return SameWorkVerdict(answer=answer, candidate=number - 1)
