"""Each compliance rule from docs/COMPLIANCE.md has a test here."""

from __future__ import annotations

import ast
import re
import time
from pathlib import Path

import httpx
import pytest
import respx

from kestrel.compliance.denylist import PolicyViolation, assert_allowed, is_denied
from kestrel.compliance.http import PoliteClient, SourceBackoff, domain_on_cooldown
from kestrel.compliance.ratelimit import DomainRateLimiter
from kestrel.models import AuditLog

PKG = Path(__file__).resolve().parents[1] / "kestrel"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/jobs/view/1",
        "https://uk.indeed.com/viewjob?jk=1",
        "https://www.indeed.co.uk/jobs",
        "https://www.glassdoor.ca/Job",
        "https://ziprecruiter.com/jobs",
        "https://www.naukri.com/x",
    ],
)
def test_denylisted_sites_are_refused(url):
    assert is_denied(url)
    with pytest.raises(PolicyViolation):
        assert_allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
        "https://api.lever.co/v0/postings/acme",
        "https://careers.example.com/jobs",
    ],
)
def test_normal_sites_are_allowed(url):
    assert not is_denied(url)
    assert_allowed(url)


def test_non_http_schemes_refused():
    with pytest.raises(PolicyViolation):
        assert_allowed("file:///etc/passwd")


async def test_polite_client_never_contacts_denylisted_site():
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*linkedin.*").respond(200)
        async with PoliteClient() as http:
            with pytest.raises(PolicyViolation):
                await http.get("https://www.linkedin.com/jobs", purpose="test", min_interval=0)
        assert not route.called
    from sqlmodel import select

    from kestrel.db import session_scope

    with session_scope() as s:
        entry = s.exec(select(AuditLog)).one()
        assert entry.blocked_reason


async def test_redirect_to_denylisted_site_is_blocked():
    with respx.mock() as mock:
        mock.get("https://example.com/jobs").respond(302, headers={"Location": "https://www.indeed.com/x"})
        async with PoliteClient() as http:
            with pytest.raises(PolicyViolation):
                await http.get("https://example.com/jobs", purpose="test", min_interval=0)


async def test_user_agent_is_honest_and_has_contact():
    with respx.mock() as mock:
        route = mock.get("https://api.example.com/x").respond(200, json={})
        async with PoliteClient() as http:
            await http.get("https://api.example.com/x", purpose="test", min_interval=0)
        ua = route.calls[0].request.headers["User-Agent"]
        assert ua.startswith("Kestrel/")
        assert "tester@example.org" in ua


async def test_retry_after_is_honoured_then_cooldown(no_sleep):
    with respx.mock() as mock:
        route = mock.get("https://api.example.com/x").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "5"}),
                httpx.Response(429, headers={"Retry-After": "5"}),
            ]
        )
        async with PoliteClient() as http:
            with pytest.raises(SourceBackoff):
                await http.get("https://api.example.com/x", purpose="test", min_interval=0)
        assert route.call_count == 2
    assert domain_on_cooldown("api.example.com")
    # While on cooldown, no request is made at all.
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get("https://api.example.com/x").respond(200)
        async with PoliteClient() as http:
            with pytest.raises(SourceBackoff):
                await http.get("https://api.example.com/x", purpose="test", min_interval=0)
        assert not route.called


async def test_long_retry_after_goes_straight_to_cooldown(no_sleep):
    with respx.mock() as mock:
        route = mock.get("https://api.example.com/y").respond(503, headers={"Retry-After": "7200"})
        async with PoliteClient() as http:
            with pytest.raises(SourceBackoff):
                await http.get("https://api.example.com/y", purpose="test", min_interval=0)
        assert route.call_count == 1


async def test_rate_limiter_spaces_requests_per_domain():
    limiter = DomainRateLimiter()
    start = time.monotonic()
    await limiter.wait("a.example", 0.2)
    await limiter.wait("a.example", 0.2)
    await limiter.wait("b.example", 0.2)  # other domains are independent
    assert time.monotonic() - start >= 0.19


async def test_audit_log_drops_query_strings():
    with respx.mock() as mock:
        mock.get(url__startswith="https://api.example.com/search").respond(200, json={})
        async with PoliteClient() as http:
            await http.get(
                "https://api.example.com/search", purpose="test", min_interval=0, params={"app_key": "SECRET"}
            )
    from sqlmodel import select

    from kestrel.db import session_scope

    with session_scope() as s:
        for entry in s.exec(select(AuditLog)).all():
            assert "SECRET" not in entry.url


# ------------------------------------------------------------------ "no auto-apply" guarantees
FORBIDDEN_CALLS = {
    "browser_click",
    "browser_type",
    "browser_set_input_files",
    "browser_download",
    "type_text",
    "click",
    "fill",
    "submit",
    "press",
    "check",
    "select_option",
    "set_input_files",
    "dispatch_event",
}


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                names.add(f.attr)
            elif isinstance(f, ast.Name):
                names.add(f.id)
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names.add(arg.value)
    return names


@pytest.mark.parametrize("path", sorted((PKG / "browse").glob("*.py")), ids=lambda p: p.name)
def test_browser_code_never_clicks_types_or_submits(path):
    assert not (_called_names(path) & FORBIDDEN_CALLS)


def test_browser_backend_interface_is_read_only():
    from kestrel.browse.base import BrowserBackend

    methods = {m for m in dir(BrowserBackend) if not m.startswith("_")}
    assert methods == {"start", "load", "read_more", "close"}


def test_no_http_post_to_job_sources():
    """Kestrel never POSTs to a job site. The only POSTs go to the user's own model endpoints."""
    allowed = {PKG / "classify" / "jev.py", PKG / "llm" / "provider.py"}
    for path in PKG.rglob("*.py"):
        if path in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        # Inbound API routes (@router.post) are fine; outbound POSTs from an HTTP client are not.
        assert not re.search(r"\b(?!router)\w+\.post\(", source), path


def test_cua_backend_refuses_interactive_tools():
    from kestrel.browse.cua import READ_ONLY_TOOLS

    assert not (READ_ONLY_TOOLS & FORBIDDEN_CALLS)


async def test_robots_redirect_to_denylisted_site_is_not_followed():
    from kestrel.compliance import robots

    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://jobs.example.org/robots.txt").respond(
            301, headers={"Location": "https://www.linkedin.com/robots.txt"}
        )
        denied = mock.get("https://www.linkedin.com/robots.txt").respond(200, text="User-agent: *\nAllow: /\n")
        assert await robots.can_fetch("https://jobs.example.org/feed.xml") is False
        assert not denied.called


async def test_robots_redirect_to_allowed_site_is_followed():
    from kestrel.compliance import robots

    with respx.mock() as mock:
        mock.get("http://jobs.example.org/robots.txt").respond(
            301, headers={"Location": "https://jobs.example.org/robots.txt"}
        )
        mock.get("https://jobs.example.org/robots.txt").respond(200, text="User-agent: *\nDisallow: /private\n")
        assert await robots.can_fetch("http://jobs.example.org/feed.xml") is True
        assert await robots.can_fetch("http://jobs.example.org/private/x") is False
