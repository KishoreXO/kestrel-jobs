"""Compliance gate wrapped around every browser backend.

Rules (all enforced here, before the backend is asked to do anything):
- Only http(s) URLs on domains the user explicitly listed in Settings → Browsing.
- Never a denylisted site (sites whose terms forbid bots), even if listed.
- robots.txt must allow the URL for our user-agent token.
- At least 10 s between page loads per domain (or the site's Crawl-delay if longer).
- A per-domain page budget per run.
- Stop the domain immediately on a CAPTCHA/bot check, a login wall, or HTTP 401/403/429,
  and keep away from it for 24 hours. We never attempt to solve or bypass any of these.
"""

from __future__ import annotations

import re
from collections import Counter

from kestrel.browse.base import BrowserBackend, PageSnapshot
from kestrel.compliance import audit, robots
from kestrel.compliance.denylist import PolicyViolation, assert_allowed, domain_of, registrable_matches
from kestrel.compliance.http import domain_on_cooldown, set_cooldown
from kestrel.compliance.ratelimit import limiter

MIN_PAGE_INTERVAL = 10.0
BLOCK_COOLDOWN = 24 * 3600

_BLOCK_PATTERNS = re.compile(
    r"captcha|are you a robot|verify (that )?you are (a )?human|unusual traffic|access denied|"
    r"bot detection|checking your browser|cf-challenge|please enable cookies to continue",
    re.I,
)
_LOGIN_PATTERNS = re.compile(r"sign in to (continue|view)|log ?in to (continue|view)|please (sign|log) in", re.I)


class DomainBlocked(PolicyViolation):
    pass


def detect_block(snapshot: PageSnapshot) -> str | None:
    if snapshot.status in (401, 403, 429):
        return f"HTTP {snapshot.status}"
    head = f"{snapshot.title}\n{snapshot.text[:3000]}"
    if _BLOCK_PATTERNS.search(head):
        return "bot check / CAPTCHA"
    if _LOGIN_PATTERNS.search(head) and len(snapshot.text) < 4000:
        return "login wall"
    return None


class GuardedBrowser:
    def __init__(self, backend: BrowserBackend, allowed_domains: list[str], max_pages_per_domain: int):
        self.backend = backend
        self.allowed = [d.lower().strip().removeprefix("www.") for d in allowed_domains if d.strip()]
        self.max_pages = max_pages_per_domain
        self.pages = Counter()
        self.current_domain = ""

    def _check_allowlist(self, url: str) -> None:
        host = domain_of(url)
        if not any(registrable_matches(host, d) for d in self.allowed):
            raise PolicyViolation(f"{host} is not in your browsing allowlist")

    async def vet(self, url: str) -> None:
        """Raises PolicyViolation if the URL may not be loaded right now."""
        assert_allowed(url)  # denylist + scheme
        self._check_allowlist(url)
        domain = domain_of(url)
        if domain_on_cooldown(domain):
            raise DomainBlocked(f"{domain} is paused after it showed a block or bot check")
        if self.pages[domain] >= self.max_pages:
            raise PolicyViolation(f"Page budget for {domain} reached ({self.max_pages})")
        if not await robots.can_fetch(url):
            raise PolicyViolation(f"robots.txt disallows {url}")

    async def load(self, url: str) -> PageSnapshot:
        try:
            await self.vet(url)
        except PolicyViolation as e:
            audit.record(url, purpose=f"browse ({self.backend.name})", blocked_reason=str(e))
            raise
        domain = domain_of(url)
        interval = max(MIN_PAGE_INTERVAL, robots.crawl_delay(url) or 0)
        await limiter.wait(f"browse:{domain}", interval)
        self.pages[domain] += 1
        snapshot = await self.backend.load(url)
        audit.record(url, purpose=f"browse ({self.backend.name})", status=snapshot.status)
        self.current_domain = domain_of(snapshot.url) or domain
        self._after_load(snapshot)
        return snapshot

    async def read_more(self) -> PageSnapshot:
        snapshot = await self.backend.read_more()
        self._after_load(snapshot)
        return snapshot

    def _after_load(self, snapshot: PageSnapshot) -> None:
        # Redirects may land somewhere else: re-check the final URL too.
        final = snapshot.url
        assert_allowed(final)
        self._check_allowlist(final)
        reason = detect_block(snapshot)
        if reason:
            domain = domain_of(final)
            set_cooldown(domain, BLOCK_COOLDOWN)
            audit.record(
                final,
                purpose=f"browse ({self.backend.name})",
                status=snapshot.status,
                blocked_reason=f"stopped: {reason}",
            )
            raise DomainBlocked(f"{domain}: {reason}. Stopped and paused this site for 24 hours.")
