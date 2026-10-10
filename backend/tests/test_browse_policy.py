"""The browser policy gate, exercised against a fake backend standing in for Cua or Playwright."""

from __future__ import annotations

import pytest
import respx

from kestrel.browse.ats_detect import detect_ats
from kestrel.browse.base import Link, PageSnapshot
from kestrel.browse.policy import DomainBlocked, GuardedBrowser
from kestrel.compliance.denylist import PolicyViolation
from kestrel.compliance.http import domain_on_cooldown


class FakeBackend:
    def __init__(self, name="fake", pages=None):
        self.name = name
        self.pages = pages or {}
        self.loaded: list[str] = []

    async def start(self):
        pass

    async def load(self, url):
        self.loaded.append(url)
        return self.pages.get(url, PageSnapshot(url=url, title="Careers", text="Open roles", status=200))

    async def read_more(self):
        return PageSnapshot(url=self.loaded[-1], text="more")

    async def close(self):
        pass


@pytest.fixture
def robots_allow_all():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/robots\.txt").respond(200, text="User-agent: *\nAllow: /\n")
        yield mock


@pytest.fixture
def fast(monkeypatch):
    from kestrel.browse import policy

    monkeypatch.setattr(policy, "MIN_PAGE_INTERVAL", 0.0)


@pytest.mark.parametrize("backend_name", ["cua", "playwright"])
async def test_allowlisted_page_loads(robots_allow_all, fast, backend_name):
    backend = FakeBackend(backend_name)
    guarded = GuardedBrowser(backend, ["example.com"], 5)
    snap = await guarded.load("https://example.com/careers")
    assert snap.title == "Careers"
    assert backend.loaded == ["https://example.com/careers"]


@pytest.mark.parametrize("backend_name", ["cua", "playwright"])
async def test_denylisted_site_refused_even_if_allowlisted(robots_allow_all, fast, backend_name):
    backend = FakeBackend(backend_name)
    guarded = GuardedBrowser(backend, ["linkedin.com"], 5)
    with pytest.raises(PolicyViolation):
        await guarded.load("https://www.linkedin.com/jobs")
    assert backend.loaded == []


async def test_site_not_in_allowlist_refused(robots_allow_all, fast):
    backend = FakeBackend()
    guarded = GuardedBrowser(backend, ["example.com"], 5)
    with pytest.raises(PolicyViolation, match="allowlist"):
        await guarded.load("https://other.org/jobs")
    assert backend.loaded == []


async def test_robots_disallow_is_respected(fast):
    with respx.mock() as mock:
        mock.get("https://example.com/robots.txt").respond(200, text="User-agent: *\nDisallow: /careers\n")
        backend = FakeBackend()
        guarded = GuardedBrowser(backend, ["example.com"], 5)
        with pytest.raises(PolicyViolation, match="robots"):
            await guarded.load("https://example.com/careers/123")
        assert backend.loaded == []


async def test_robots_disallow_for_our_token(fast):
    with respx.mock() as mock:
        mock.get("https://example.com/robots.txt").respond(
            200, text="User-agent: Kestrel\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
        )
        guarded = GuardedBrowser(FakeBackend(), ["example.com"], 5)
        with pytest.raises(PolicyViolation):
            await guarded.load("https://example.com/")


async def test_unreadable_robots_means_no_crawling(fast):
    with respx.mock() as mock:
        mock.get("https://example.com/robots.txt").respond(503)
        guarded = GuardedBrowser(FakeBackend(), ["example.com"], 5)
        with pytest.raises(PolicyViolation):
            await guarded.load("https://example.com/")


async def test_captcha_stops_and_pauses_domain(robots_allow_all, fast):
    url = "https://example.com/careers"
    backend = FakeBackend(
        pages={
            url: PageSnapshot(url=url, title="Just a moment", text="Please verify you are human (captcha)", status=200)
        }
    )
    guarded = GuardedBrowser(backend, ["example.com"], 5)
    with pytest.raises(DomainBlocked):
        await guarded.load(url)
    assert domain_on_cooldown("example.com")
    with pytest.raises(DomainBlocked):
        await guarded.load("https://example.com/other")
    assert backend.loaded == [url]


@pytest.mark.parametrize("status", [401, 403, 429])
async def test_http_block_statuses_stop(robots_allow_all, fast, status):
    url = "https://example.com/x"
    backend = FakeBackend(pages={url: PageSnapshot(url=url, text="nope", status=status)})
    with pytest.raises(DomainBlocked):
        await GuardedBrowser(backend, ["example.com"], 5).load(url)


async def test_redirect_off_allowlist_is_caught(robots_allow_all, fast):
    url = "https://example.com/jobs"
    backend = FakeBackend(pages={url: PageSnapshot(url="https://www.indeed.com/cmp/x", text="", status=200)})
    with pytest.raises(PolicyViolation):
        await GuardedBrowser(backend, ["example.com"], 5).load(url)


async def test_page_budget(robots_allow_all, fast):
    guarded = GuardedBrowser(FakeBackend(), ["example.com"], 2)
    await guarded.load("https://example.com/a")
    await guarded.load("https://example.com/b")
    with pytest.raises(PolicyViolation, match="budget"):
        await guarded.load("https://example.com/c")


@pytest.mark.parametrize(
    ("url", "ats", "slug"),
    [
        ("https://boards.greenhouse.io/acme/jobs/123", "greenhouse", "acme"),
        ("https://job-boards.greenhouse.io/acme", "greenhouse", "acme"),
        ("https://jobs.lever.co/acme/abc-123", "lever", "acme"),
        ("https://jobs.eu.lever.co/acme", "lever", "acme"),
        ("https://jobs.ashbyhq.com/acme", "ashby", "acme"),
    ],
)
def test_ats_detection(url, ats, slug):
    c = detect_ats([url])
    assert c and c.ats == ats and c.slug == slug


def test_ats_detection_none():
    assert detect_ats(["https://example.com/careers"]) is None


def test_link_type():
    assert Link("a", "b").href == "b"
