"""Shared test setup: tests run without the developer's settings.

Without this, a test that reaches get_settings() reads the local .env: locally it can
make real requests with the developer's keys (spending quota) and pass, while CI, which
has no .env, fails. Tests marked `live` keep the real settings.
"""

from collections.abc import Iterator

import pytest

from book_agent.config import Settings, get_settings


@pytest.fixture(autouse=True)
def no_local_settings(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    if request.node.get_closest_marker("live") is not None:
        yield
        return
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
