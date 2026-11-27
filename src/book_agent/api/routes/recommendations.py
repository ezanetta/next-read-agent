"""The streamed recommendation (Server-Sent Events)."""

import asyncio
import contextlib
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from book_agent.api.dependencies import Services
from book_agent.api.schemas import ErrorBody
from book_agent.demo_profiles import find_profile
from book_agent.domain.recommendations import Filters
from book_agent.domain.requests import FilterName
from book_agent.recommendation_stream import (
    MAX_REQUEST_CHARS,
    RecommendationEvent,
    recommendation_events,
)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

KEEPALIVE_S = 15.0
EVENT_SCHEMA = "RecommendationEvent"


class RecommendationRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=64)
    request: str | None = Field(default=None, max_length=MAX_REQUEST_CHARS)
    filters: Filters = Filters()
    # Interpreted filters the user removed from the chips: the request runs again without
    # them, and counts as a run (D-045).
    ignore_filters: list[FilterName] = Field(default_factory=list[FilterName], max_length=3)


@router.post(
    "",
    operation_id="streamRecommendation",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Server-Sent Events: one `RecommendationEvent` per `data:` line, "
            "with the event's `type` as the SSE event name. Ends with `done` or `failed`.",
            "content": {
                "text/event-stream": {"schema": {"$ref": f"#/components/schemas/{EVENT_SCHEMA}"}}
            },
        },
        404: {"model": ErrorBody, "description": "No such profile, or it expired"},
    },
)
async def stream_recommendation(
    body: RecommendationRequest, services: Services
) -> StreamingResponse:
    """Recommend 3 books for a profile, streaming the agent's progress as it happens."""
    stored = find_profile(body.profile_id, services.presets, services.profiles, services.clock())
    if stored is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "profile not found"})
    events = recommendation_events(
        stored,
        body.request.strip() if body.request and body.request.strip() else None,
        body.filters,
        llm=services.llm,
        books=services.books,
        works=services.works,
        prompt=services.prompt,
        ignore_filters=body.ignore_filters,
        deadline_s=services.run_deadline_s,
        stop=services.draining,
    )
    return StreamingResponse(
        sse(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def sse(
    events: AsyncGenerator[RecommendationEvent], keepalive_s: float = KEEPALIVE_S
) -> AsyncGenerator[str]:
    """SSE framing, with a comment line while nothing happens so proxies keep the
    connection open. If the client leaves, closing `events` cancels the run."""
    pending: asyncio.Future[RecommendationEvent] | None = None
    try:
        while True:
            pending = asyncio.ensure_future(anext(events))
            while not (await asyncio.wait({pending}, timeout=keepalive_s))[0]:
                yield ": keep-alive\n\n"
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        await events.aclose()
