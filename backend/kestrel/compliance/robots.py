"""robots.txt checks for the optional browser agent."""

from __future__ import annotations

import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from kestrel.compliance import audit
from kestrel.compliance.denylist import is_denied
from kestrel.compliance.useragent import ROBOTS_TOKEN, user_agent

_CACHE_TTL = 6 * 3600
MAX_REDIRECTS = 3
_cache: dict[str, tuple[float, RobotFileParser | None]] = {}


async def _load(origin: str) -> RobotFileParser | None:
    """Returns a parser, or None when robots.txt could not be read due to a server error/denial."""
    url = f"{origin}/robots.txt"
    parser = RobotFileParser(url)
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": user_agent()}) as client:
            resp = await client.get(url)
            audit.record(url, purpose="robots.txt", status=resp.status_code)
            # Redirects are followed by hand so that every hop passes the denylist.
            for _ in range(MAX_REDIRECTS):
                if not (resp.is_redirect and resp.next_request):
                    break
                url = str(resp.next_request.url)
                if is_denied(url):
                    audit.record(url, purpose="robots.txt", blocked_reason="redirect to a denylisted site")
                    return None
                resp = await client.get(url)
                audit.record(url, purpose="robots.txt", status=resp.status_code)
    except httpx.HTTPError:
        audit.record(url, purpose="robots.txt", blocked_reason="unreachable")
        return None
    if resp.is_redirect:
        return None  # too many redirects: treat as "disallow everything"
    if resp.status_code in (401, 403) or resp.status_code >= 500:
        return None  # Treat as "disallow everything" (conservative).
    if resp.status_code >= 400:
        parser.parse([])  # No robots.txt: everything allowed.
        return parser
    parser.parse(resp.text.splitlines())
    return parser


async def can_fetch(url: str) -> bool:
    if is_denied(url):
        return False  # never contact a denylisted site, not even for robots.txt
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    cached = _cache.get(origin)
    if cached is None or time.time() - cached[0] > _CACHE_TTL:
        cached = (time.time(), await _load(origin))
        _cache[origin] = cached
    parser = cached[1]
    if parser is None:
        return False
    return parser.can_fetch(ROBOTS_TOKEN, url)


def crawl_delay(url: str) -> float | None:
    parts = urlsplit(url)
    cached = _cache.get(f"{parts.scheme}://{parts.netloc}")
    if not cached or cached[1] is None:
        return None
    delay = cached[1].crawl_delay(ROBOTS_TOKEN)
    return float(delay) if delay else None


def clear_cache() -> None:
    _cache.clear()
