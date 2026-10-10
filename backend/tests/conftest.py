from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """Every test gets its own data dir / SQLite DB and fresh caches."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTACT_EMAIL", "tester@example.org")
    for key in ("TYPESAFE_API_KEY", "LLM_MODEL", "LLM_API_KEY", "CLASSIFIER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)  # ignore any developer .env

    from kestrel.compliance import robots
    from kestrel.compliance.ratelimit import limiter
    from kestrel.config import get_settings
    from kestrel.db import get_engine
    from kestrel.llm import provider

    get_settings.cache_clear()
    get_engine.cache_clear()
    robots.clear_cache()
    limiter.reset()
    provider._SERVER_QUIRKS.clear()
    yield
    get_engine.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def no_sleep(monkeypatch):
    import asyncio

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
