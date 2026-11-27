"""Request body size limits, enforced while the body arrives (R-005, D-043).

Starlette parses a whole multipart upload (spooling it to disk) before a route sees it,
so checking the size in the route is too late. This ASGI middleware rejects a body over
its route's limit with 413: at once when Content-Length says so, or as soon as the
bytes read so far exceed it (chunked uploads), without reading the rest.
"""

import json
from collections.abc import Mapping

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_JSON_BODY = 64 * 1024  # D-043: every JSON body the API takes is far smaller
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


class _TooLarge(BaseException):
    """Raised from `receive` when the body passes the limit. A BaseException on purpose:
    FastAPI turns any Exception raised while reading a body into a 400."""


class BodySizeLimit:
    """413 for bodies over `limits[path]`, or `default` for other paths."""

    def __init__(
        self, app: ASGIApp, *, default: int = MAX_JSON_BODY, limits: Mapping[str, int] = {}
    ) -> None:
        self._app = app
        self._default = default
        self._limits = dict(limits)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in BODY_METHODS:
            await self._app(scope, receive, send)
            return
        limit = self._limits.get(scope["path"], self._default)
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _reject(send, limit)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _TooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not response_started:  # always the case: bodies are read before answering
                await _reject(send, limit)


async def _reject(send: Send, limit: int) -> None:
    body = json.dumps(
        {"error": "too_large", "message": f"the request body is over {limit // 1024} KB"}
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
