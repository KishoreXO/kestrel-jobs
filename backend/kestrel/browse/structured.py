"""Lightweight, browser-free reading of careers pages.

Many careers pages already publish their openings as schema.org `JobPosting` data
(`<script type="application/ld+json">`), the same markup job search engines read. Fetching that
with a plain, polite HTTP request is far lighter for the site than driving a browser, so this
runs first; a browser backend is only used when a page has no structured data at all.

Every request goes through PoliteClient (denylist, cooldowns, rate limit, audit) after a
robots.txt check, stays on the allowlisted site, and stops on 401/403/429 or a bot check.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urldefrag, urljoin

from kestrel.compliance import robots
from kestrel.compliance.denylist import PolicyViolation, assert_allowed, domain_of, registrable_matches
from kestrel.compliance.http import PoliteClient, SourceBackoff, set_cooldown
from kestrel.sources.base import JobPosting, html_to_text, parse_dt

MIN_INTERVAL = 10.0  # seconds between page loads on the same site
BLOCK_COOLDOWN = 24 * 3600
MAX_PAGE_BYTES = 3_000_000

_LDJSON = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
_HREF = re.compile(r"<a\b[^>]*\bhref=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_BOT_CHECK = re.compile(r"captcha|are you a robot|verify you are human|access denied|cf-challenge", re.I)
_JOBBY = re.compile(r"job|career|position|opening|vacanc|role", re.I)
_SKIP = re.compile(r"\.(pdf|jpg|jpeg|png|gif|svg|zip|docx?)$|mailto:|tel:|javascript:|/login|/signin|/apply", re.I)


def _short_id(text: str) -> str:
    return hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:16]


class SiteBlocked(Exception):
    """The site refused or challenged us; it is paused and must not be retried."""


@dataclass
class PageRead:
    url: str
    postings: list[JobPosting] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)  # (text, absolute url)


def _iter_objects(node):
    """Yields every dict in a JSON-LD document (handles @graph, ItemList and nested lists)."""
    if isinstance(node, list):
        for item in node:
            yield from _iter_objects(item)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "item"):
            if key in node:
                yield from _iter_objects(node[key])


def _is_type(obj: dict, name: str) -> bool:
    t = obj.get("@type")
    return name in t if isinstance(t, list) else t == name


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("@value") or "")
    if isinstance(value, list):
        return ", ".join(filter(None, (_text(v) for v in value)))
    return str(value or "")


def _location(obj: dict) -> tuple[str, bool | None]:
    remote = True if str(obj.get("jobLocationType", "")).upper() == "TELECOMMUTE" else None
    places = obj.get("jobLocation") or []
    parts = []
    for place in places if isinstance(places, list) else [places]:
        if not isinstance(place, dict):
            continue
        addr = place.get("address") or {}
        if isinstance(addr, dict):
            bits = [_text(addr.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry")]
            parts.append(", ".join(b for b in bits if b))
        else:
            parts.append(_text(addr))
    location = "; ".join(p for p in parts if p)
    if not location and remote:
        location = "Remote"
    return location, remote


def _salary(obj: dict) -> str:
    base = obj.get("baseSalary")
    if not isinstance(base, dict):
        return _text(base)
    value = base.get("value") or {}
    currency = base.get("currency", "")
    if isinstance(value, dict):
        lo, hi, unit = value.get("minValue"), value.get("maxValue"), value.get("unitText", "")
        amount = f"{lo}–{hi}" if lo and hi else str(lo or hi or value.get("value") or "")
        return " ".join(b for b in (currency, amount, f"per {unit.lower()}" if unit else "") if b).strip()
    return f"{currency} {value}".strip()


def parse_job_postings(html: str, page_url: str) -> list[JobPosting]:
    """Extracts schema.org JobPosting objects from a page's JSON-LD blocks."""
    out: list[JobPosting] = []
    for raw in _LDJSON.findall(html):
        try:
            doc = json.loads(unescape(raw.strip()))
        except json.JSONDecodeError:
            continue
        for obj in _iter_objects(doc):
            if not _is_type(obj, "JobPosting") or not obj.get("title"):
                continue
            url = urljoin(page_url, str(obj.get("url") or page_url))  # sites often publish "/jobs/123"
            location, remote = _location(obj)
            org = obj.get("hiringOrganization")
            company = _text(org) if org else ""
            posted = None
            try:
                posted = parse_dt(obj.get("datePosted")) if obj.get("datePosted") else None
            except (ValueError, TypeError):
                posted = None
            ident = obj.get("identifier")
            ident = ident.get("value") if isinstance(ident, dict) else ident
            out.append(
                JobPosting(
                    source="web",
                    external_id=_short_id(f"{url}|{ident or obj['title']}"),
                    company=company or domain_of(page_url).removeprefix("www.").split(".")[0].capitalize(),
                    title=html_to_text(str(obj["title"])),
                    url=url,
                    apply_url=url,
                    location=location,
                    remote=remote,
                    description=html_to_text(str(obj.get("description") or ""))[:20000],
                    salary=_salary(obj),
                    department=_text(obj.get("occupationalCategory") or obj.get("industry")),
                    posted_at=posted,
                    attribution_text=f"Read from {domain_of(url)}",
                    attribution_url=url,
                )
            )
    return out


def page_links(html: str, base_url: str) -> list[tuple[str, str]]:
    out = []
    for href, inner in _HREF.findall(html):
        absolute, _ = urldefrag(urljoin(base_url, unescape(href.strip())))
        if absolute.startswith(("http://", "https://")):
            out.append((_TAGS.sub(" ", inner).strip()[:120], absolute))
    return out


async def read_page(http: PoliteClient, url: str, site_domain: str) -> PageRead | None:
    """Fetches one page politely. Returns None when robots.txt disallows it."""
    assert_allowed(url)  # denylist first: a denylisted site never even gets a robots.txt request
    if not registrable_matches(domain_of(url), site_domain):
        raise PolicyViolation(f"{url} is outside the allowlisted site {site_domain}")
    if not await robots.can_fetch(url):
        return None
    interval = max(MIN_INTERVAL, robots.crawl_delay(url) or 0)
    try:
        resp = await http.get(
            url,
            purpose="careers page (structured data)",
            min_interval=interval,
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
    except SourceBackoff as e:
        raise SiteBlocked(str(e)) from e
    final = str(resp.url)
    if resp.status_code in (401, 403, 429):
        set_cooldown(domain_of(url), BLOCK_COOLDOWN)
        raise SiteBlocked(f"{site_domain} answered {resp.status_code}; paused for 24 hours")
    if resp.status_code >= 400 or not registrable_matches(domain_of(final), site_domain):
        return PageRead(url=final)
    html = resp.text[:MAX_PAGE_BYTES]
    if _BOT_CHECK.search(html[:20000]) and not _LDJSON.search(html):
        set_cooldown(domain_of(url), BLOCK_COOLDOWN)
        raise SiteBlocked(f"{site_domain} showed a bot check; paused for 24 hours (never bypassed)")
    return PageRead(url=final, postings=parse_job_postings(html, final), links=page_links(html, final))


def job_links(links: list[tuple[str, str]], site_domain: str, seen: set[str]) -> list[str]:
    """Same-site links that look like they lead to job postings, most promising first."""
    picked = []
    for text, url in sorted(links, key=lambda tu: -len(_JOBBY.findall(tu[0] + " " + tu[1]))):
        if url in seen or _SKIP.search(url) or not registrable_matches(domain_of(url), site_domain):
            continue
        if _JOBBY.search(text + " " + url):
            seen.add(url)
            picked.append(url)
    return picked
