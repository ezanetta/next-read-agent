"""Reading JSON out of an LLM reply that may wrap it in code fences or prose.

Shared by every parser of model output (recommendations, request interpretation).
"""

import json
import re
from typing import cast

from pydantic import BaseModel, ValidationError

_FENCED = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class ReplyParseError(ValueError):
    """The reply could not be read as the expected JSON. The message says why."""


def extract_json(text: str) -> object:
    """Return the first JSON value found in `text`.

    Tries, in order: the content of a ``` fence, the whole text, and the outermost
    {...} block. Only candidates that start with { or [ are parsed, so plain prose
    reports "no JSON found" rather than a confusing syntax error.
    """
    candidates: list[str] = []
    fenced = _FENCED.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text.strip())
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])

    error = "no JSON found in the reply"
    for candidate in candidates:
        if not candidate.startswith(("{", "[")):
            continue
        try:
            return cast(object, json.loads(candidate))
        except json.JSONDecodeError as e:
            error = f"invalid JSON ({e.msg} at line {e.lineno}, column {e.colno})"
    raise ReplyParseError(error)


def parse_model[M: BaseModel](model: type[M], payload: object) -> M:
    """Validate a JSON value into `model`, raising ReplyParseError with the first problem."""
    try:
        return model.model_validate(payload)
    except ValidationError as e:
        first = e.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "reply"
        more = f" (and {e.error_count() - 1} more)" if e.error_count() > 1 else ""
        raise ReplyParseError(f"unexpected JSON shape at {location}: {first['msg']}{more}") from e
