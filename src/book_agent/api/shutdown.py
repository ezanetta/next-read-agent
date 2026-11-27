"""Telling open streams the server is stopping, while they can still say so (R-004).

On SIGTERM (a revision rollout, scale to zero) uvicorn stops accepting connections and
waits for open ones to finish; only after its graceful-shutdown timeout does it cancel
them, and a cancelled stream can't send anything. This chains a handler in front of
uvicorn's own for SIGTERM and SIGINT: it sets an event that every recommendation stream
watches, so each one cancels its run and ends with a `failed` event right away, and the
server shuts down quickly. uvicorn's handler still runs, unchanged.
"""

import asyncio
import signal
import threading
from collections.abc import Callable
from types import FrameType

HANDLED_SIGNALS = (signal.SIGTERM, signal.SIGINT)

Handler = Callable[[int, FrameType | None], object] | int | signal.Handlers | None


def drain_on_signals(draining: asyncio.Event) -> Callable[[], None]:
    """Set `draining` on SIGTERM or SIGINT, then run the previous handler. Returns a
    function that restores the previous handlers. Signals can only be handled in the
    main thread: elsewhere (some test runners) it does nothing."""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    loop = asyncio.get_running_loop()
    previous: dict[int, Handler] = {sig: signal.getsignal(sig) for sig in HANDLED_SIGNALS}

    def handler(signum: int, frame: FrameType | None) -> None:
        loop.call_soon_threadsafe(draining.set)
        before = previous[signum]
        if callable(before):
            before(signum, frame)
        elif before == signal.SIG_DFL:  # no server handler: keep the default behavior
            signal.signal(signum, signal.SIG_DFL)
            signal.raise_signal(signum)

    for sig in HANDLED_SIGNALS:
        signal.signal(sig, handler)

    def restore() -> None:
        for sig, before in previous.items():
            if before is not None:
                signal.signal(sig, before)

    return restore
