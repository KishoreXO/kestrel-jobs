"""Sites whose terms of service prohibit bots, crawlers or automated access.

This list is enforced before *every* outbound request (API client and browser alike) and is
intentionally not configurable. To remove an entry, open a pull request that links to the
site's written permission or updated terms. See docs/COMPLIANCE.md.
"""

from __future__ import annotations

from urllib.parse import urlsplit

DENYLIST: frozenset[str] = frozenset(
    {
        "linkedin.com",
        "lnkd.in",
        "indeed.com",
        "glassdoor.com",
        "ziprecruiter.com",
        "monster.com",
        "dice.com",
        "wellfound.com",
        "angel.co",
        "naukri.com",
        "simplyhired.com",
        "careerbuilder.com",
        "seek.com.au",
        "reed.co.uk",
        "totaljobs.com",
        "stepstone.de",
        "xing.com",
        "jobstreet.com",
        "handshake.com",
        "joinhandshake.com",
        "upwork.com",
        "fiverr.com",
        "google.com",
    }
)


BRANDS_WITH_COUNTRY_SITES = {"linkedin", "indeed", "glassdoor", "ziprecruiter", "monster", "naukri", "stepstone"}


class PolicyViolation(Exception):
    """Raised when a request would break a compliance rule. Never caught silently."""


def domain_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def registrable_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_denied(url: str) -> bool:
    host = domain_of(url)
    if not host:
        return True
    # Also catches country variants such as indeed.co.uk / glassdoor.ca / linkedin.cn.
    labels = host.split(".")
    for denied in DENYLIST:
        if registrable_matches(host, denied):
            return True
        brand = denied.split(".")[0]
        if brand in BRANDS_WITH_COUNTRY_SITES and brand in labels:
            return True
    return False


def assert_allowed(url: str) -> None:
    if urlsplit(url).scheme not in ("http", "https"):
        raise PolicyViolation(f"Only http(s) URLs are allowed: {url}")
    if is_denied(url):
        raise PolicyViolation(f"{domain_of(url)} prohibits automated access in its terms; Kestrel never contacts it.")
