"""Browser-free reading of schema.org JobPosting data, and the rules it must follow."""

from __future__ import annotations

import json

import pytest
import respx

from kestrel.browse.structured import SiteBlocked, job_links, page_links, parse_job_postings, read_page
from kestrel.compliance import robots
from kestrel.compliance.denylist import PolicyViolation
from kestrel.compliance.http import PoliteClient, domain_on_cooldown

POSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Backend Engineer",
    "description": "<p>Build <b>APIs</b> in Python.</p>",
    "datePosted": "2026-09-01",
    "url": "https://example.com/careers/backend",
    "hiringOrganization": {"@type": "Organization", "name": "Example Co"},
    "jobLocation": {"@type": "Place", "address": {"addressLocality": "Berlin", "addressCountry": "DE"}},
    "baseSalary": {"currency": "EUR", "value": {"minValue": 70000, "maxValue": 90000, "unitText": "YEAR"}},
}


def _page(*objs) -> str:
    blocks = "".join(f'<script type="application/ld+json">{json.dumps(o)}</script>' for o in objs)
    return f"<html><head>{blocks}</head><body><a href='/careers/2'>Open roles</a></body></html>"


def test_parse_plain_posting():
    [p] = parse_job_postings(_page(POSTING), "https://example.com/careers")
    assert p.title == "Backend Engineer"
    assert p.company == "Example Co"
    assert p.location == "Berlin, DE"
    assert p.description == "Build APIs in Python."
    assert "70000" in p.salary and "EUR" in p.salary
    assert p.url == "https://example.com/careers/backend"
    assert p.posted_at is not None and p.posted_at.tzinfo is not None


def test_parse_graph_and_itemlist():
    graph = {"@context": "https://schema.org", "@graph": [{"@type": "Organization", "name": "X"}, POSTING]}
    items = {
        "@type": "ItemList",
        "itemListElement": [
            {"@type": "ListItem", "item": {**POSTING, "title": "Data Analyst", "url": "https://example.com/c/da"}},
        ],
    }
    remote = {**POSTING, "title": "Remote SRE", "jobLocationType": "TELECOMMUTE", "jobLocation": None}
    titles = [p.title for p in parse_job_postings(_page(graph, items, remote), "https://example.com/careers")]
    assert titles == ["Backend Engineer", "Data Analyst", "Remote SRE"]
    assert parse_job_postings(_page(remote), "https://example.com/")[0].remote is True


def test_broken_json_and_other_types_are_ignored():
    html = '<script type="application/ld+json">{not json</script>' + _page({"@type": "Organization"})
    assert parse_job_postings(html, "https://example.com/") == []


def test_job_links_stay_on_site():
    links = page_links(
        '<a href="/jobs/1">Engineer</a><a href="https://other.com/jobs">x</a>'
        '<a href="/about">About</a><a href="/apply/1">Apply</a>',
        "https://example.com/careers",
    )
    assert job_links(links, "example.com", set()) == ["https://example.com/jobs/1"]


@pytest.fixture
def no_wait(monkeypatch):
    from kestrel.browse import structured

    monkeypatch.setattr(structured, "MIN_INTERVAL", 0.0)


async def test_read_page_uses_polite_client(no_wait):
    with respx.mock() as mock:
        mock.get("https://example.com/robots.txt").respond(200, text="User-agent: *\nAllow: /\n")
        route = mock.get("https://example.com/careers").respond(200, text=_page(POSTING))
        async with PoliteClient() as http:
            page = await read_page(http, "https://example.com/careers", "example.com")
        assert [p.title for p in page.postings] == ["Backend Engineer"]
        assert route.calls[0].request.headers["User-Agent"].startswith("Kestrel/")


async def test_read_page_respects_robots(no_wait):
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://example.com/robots.txt").respond(200, text="User-agent: Kestrel\nDisallow: /\n")
        page_route = mock.get("https://example.com/careers")
        async with PoliteClient() as http:
            assert await read_page(http, "https://example.com/careers", "example.com") is None
        assert not page_route.called


async def test_read_page_refuses_denylisted_and_off_site(no_wait):
    # respx with no routes: any real request (even for robots.txt) would fail the test.
    with respx.mock(assert_all_called=False):
        http = PoliteClient()
        with pytest.raises(PolicyViolation):
            await read_page(http, "https://www.linkedin.com/jobs", "linkedin.com")
        assert not await robots.can_fetch("https://www.indeed.com/jobs")
        with pytest.raises(PolicyViolation):
            await read_page(http, "https://other.com/jobs", "example.com")


async def test_403_pauses_site(no_wait):
    with respx.mock() as mock:
        mock.get("https://example.com/robots.txt").respond(200, text="")
        mock.get("https://example.com/careers").respond(403)
        async with PoliteClient() as http:
            with pytest.raises(SiteBlocked):
                await read_page(http, "https://example.com/careers", "example.com")
    assert domain_on_cooldown("example.com")


async def test_bot_check_pauses_site(no_wait):
    with respx.mock() as mock:
        mock.get("https://example.com/robots.txt").respond(200, text="")
        mock.get("https://example.com/careers").respond(200, text="<h1>Verify you are human</h1> captcha")
        async with PoliteClient() as http:
            with pytest.raises(SiteBlocked):
                await read_page(http, "https://example.com/careers", "example.com")


def test_relative_posting_links_become_absolute():
    [p] = parse_job_postings(_page({**POSTING, "url": "/careers/backend"}), "https://example.com/careers")
    assert p.url == "https://example.com/careers/backend"


async def test_unreachable_site_is_skipped_not_fatal(no_wait):
    import httpx

    from kestrel.browse.agent import read_structured

    said: list[str] = []
    with respx.mock() as mock:
        mock.get("https://example.com/robots.txt").respond(200, text="User-agent: *\nAllow: /\n")
        mock.get("https://example.com/careers").mock(side_effect=httpx.ConnectError("down"))
        async with PoliteClient() as http:
            found = await read_structured(http, "example.com", "https://example.com/careers", "Example", said.append)
    assert found is None
    assert said and "Couldn't reach example.com" in said[0]
