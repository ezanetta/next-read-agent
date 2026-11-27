"""Keeping readers' libraries out of the logs (R-006).

A reading history is personal data, and the demo promises to keep it only for a few days
(D-043); logs, exported by OpenTelemetry, would outlive that. So logs carry counts,
indexes, error types and short hashes, never titles, authors, request text or profile
ids (a profile id is its only access key). Two library loggers need help:
- httpx logs every request URL at INFO, and Open Library search URLs contain titles and
  authors: it's raised to WARNING.
- uvicorn's access log records each path with its query string: queries are dropped and
  profile ids replaced by `{id}`.
"""

import hashlib
import logging
import re
import traceback

QUIET_LOGGERS = ("httpx", "httpcore")
_PROFILE_PATH = re.compile(r"^/profiles/(?!goodreads$|manual$|presets$)[^/?]+")


def ref(profile_id: str) -> str:
    """A short, stable stand-in for a profile id in logs (8 hex characters)."""
    return hashlib.sha256(profile_id.encode()).hexdigest()[:8]


def frames(error: BaseException) -> str:
    """Where an exception was raised, without its message (which may quote user data)."""
    return "".join(traceback.format_tb(error.__traceback__)).rstrip()


def redact_path(path: str) -> str:
    """An access-log path without its query string, and with profile ids as `{id}`."""
    return _PROFILE_PATH.sub("/profiles/{id}", path.split("?", 1)[0])


class AccessLogRedactor(logging.Filter):
    """Rewrites uvicorn access-log records: (client, method, path, version, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (args[0], args[1], redact_path(args[2]), args[3], args[4])
        return True


def install() -> None:
    """Apply both rules. Safe to call more than once."""
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessLogRedactor) for f in access.filters):
        access.addFilter(AccessLogRedactor())
