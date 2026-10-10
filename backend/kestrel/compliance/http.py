"""The only HTTP client Kestrel uses to talk to job sources.

Every request goes through the same gate: denylist → per-domain cooldown → rate limit →
honest User-Agent → audit log → Retry-After handling.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx

from kestrel.compliance import audit
from kestrel.compliance.denylist import PolicyViolation, assert_allowed, domain_of
from kestrel.compliance.ratelimit import limiter
from kestrel.compliance.useragent import user_agent
from kestrel.db import session_scope
from kestrel.models import FetchState

MAX_INLINE_RETRY_AFTER = 60  # seconds; longer back-offs put the domain on cooldown instead.


class SourceBackoff(Exception):
    """The source asked us to slow down; we stop contacting it until the cooldown passes."""


def _retry_after_seconds(resp: httpx.Response) -> float:
    value = resp.headers.get("Retry-After", "")
    if not value:
        return 120.0
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            return max(0.0, (when - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError):
            return 120.0


def _cooldown_key(domain: str) -> str:
    return f"cooldown:{domain}"


def domain_on_cooldown(domain: str) -> datetime | None:
    with session_scope() as session:
        state = session.get(FetchState, _cooldown_key(domain))
        if state and state.cooldown_until:
            until = state.cooldown_until
            if until.tzinfo is None:
                until = until.replace(tzinfo=UTC)
            if until > datetime.now(UTC):
                return until
    return None


def set_cooldown(domain: str, seconds: float) -> None:
    with session_scope() as session:
        key = _cooldown_key(domain)
        state = session.get(FetchState, key) or FetchState(key=key)
        state.cooldown_until = datetime.now(UTC) + timedelta(seconds=seconds)
        session.add(state)
        session.commit()


class PoliteClient:
    def __init__(self, *, timeout: float = 30.0, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False)

    async def __aenter__(self) -> PoliteClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def get(
        self,
        url: str,
        *,
        purpose: str,
        min_interval: float,
        params: dict | None = None,
        headers: dict | None = None,
        max_redirects: int = 3,
    ) -> httpx.Response:
        for _ in range(max_redirects + 1):
            resp = await self._get_once(url, purpose=purpose, min_interval=min_interval, params=params, headers=headers)
            if resp.is_redirect and "location" in resp.headers:
                # Re-check the denylist on every hop.
                url = str(resp.next_request.url) if resp.next_request else resp.headers["location"]
                params = None
                continue
            return resp
        raise PolicyViolation(f"Too many redirects for {url}")

    async def _get_once(self, url, *, purpose, min_interval, params, headers) -> httpx.Response:
        try:
            assert_allowed(url)
        except PolicyViolation as e:
            audit.record(url, purpose=purpose, blocked_reason=str(e))
            raise
        domain = domain_of(url)
        until = domain_on_cooldown(domain)
        if until:
            audit.record(url, purpose=purpose, blocked_reason=f"cooldown until {until.isoformat()}")
            raise SourceBackoff(f"{domain} asked us to back off until {until.isoformat()}")

        merged = {"User-Agent": user_agent(), "Accept": "application/json"}
        merged.update(headers or {})

        for attempt in range(2):
            await limiter.wait(domain, min_interval)
            resp = await self._client.get(url, params=params, headers=merged)
            audit.record(str(resp.request.url), purpose=purpose, status=resp.status_code)
            if resp.status_code in (429, 503):
                wait = _retry_after_seconds(resp)
                if attempt == 0 and wait <= MAX_INLINE_RETRY_AFTER:
                    await asyncio.sleep(wait)
                    continue
                set_cooldown(domain, max(wait, 3600))
                raise SourceBackoff(f"{domain} returned {resp.status_code}; pausing this source")
            return resp
        raise SourceBackoff(f"{domain} kept rate limiting us")
