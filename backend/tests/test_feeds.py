from __future__ import annotations

import json

import pytest
import respx
from fastapi.testclient import TestClient

from kestrel.compliance.http import PoliteClient
from kestrel.sources.base import SearchQuery
from kestrel.sources.feeds import Feed, FeedSource, load_feeds, parse_feed, save_feeds

RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Example Jobs</title>
    <link>https://jobs.example.org/</link>
    <item>
      <title>Backend Engineer</title>
      <link>https://jobs.example.org/1</link>
      <guid>job-1</guid>
      <pubDate>Tue, 01 Sep 2026 10:00:00 GMT</pubDate>
      <content:encoded>&lt;p&gt;Python and SQL&lt;/p&gt;</content:encoded>
    </item>
    <item>
      <title>Sneaky</title>
      <link>javascript:alert(1)</link>
    </item>
  </channel>
</rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Jobs</title>
  <link rel="alternate" href="https://atom.example.org/"/>
  <entry>
    <id>urn:job:7</id>
    <title>Data Engineer</title>
    <link href="/jobs/7"/>
    <updated>2026-09-02T00:00:00Z</updated>
    <summary>Build pipelines</summary>
  </entry>
</feed>"""

JSON_FEED = {
    "version": "https://jsonfeed.org/version/1.1",
    "home_page_url": "https://json.example.org/",
    "items": [
        {"id": "a", "url": "https://json.example.org/a", "title": "ML Engineer", "content_text": "Train models"},
        {"id": "b", "title": "No link"},
    ],
}

BOMB = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<rss><channel><item><title>&lol2;</title></item></channel></rss>"""


def feed(name="Example Jobs", url="https://jobs.example.org/feed.xml") -> Feed:
    return Feed(name=name, url=url, allowed=True)


def test_rss_items_become_postings_with_attribution():
    (job,) = parse_feed(feed(), RSS, "application/rss+xml")  # the javascript: item is dropped
    assert job.title == "Backend Engineer"
    assert job.url == "https://jobs.example.org/1"
    assert job.description == "Python and SQL"
    assert job.attribution_text == "via Example Jobs"
    assert job.attribution_url == "https://jobs.example.org/"
    assert job.posted_at and job.posted_at.year == 2026


def test_atom_and_json_feeds():
    (a,) = parse_feed(feed("Atom Jobs", "https://atom.example.org/feed"), ATOM)
    assert a.url == "https://atom.example.org/jobs/7"  # relative links resolve against the feed
    assert a.description == "Build pipelines"
    (j,) = parse_feed(feed("JSON Jobs", "https://json.example.org/feed.json"), json.dumps(JSON_FEED).encode())
    assert j.title == "ML Engineer" and j.attribution_url == "https://json.example.org/"


def test_xml_entity_tricks_are_refused():
    with pytest.raises(ValueError, match="safety"):
        parse_feed(feed(), BOMB)


def test_oversized_and_unknown_documents_are_refused():
    with pytest.raises(ValueError, match="1 MB"):
        parse_feed(feed(), b"<rss>" + b" " * 1_000_001)
    with pytest.raises(ValueError, match="not an RSS"):
        parse_feed(feed(), b"<html><body>hi</body></html>")


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (Feed("Unconfirmed", "https://jobs.example.org/feed.xml"), "confirm"),
        (Feed("Script", "javascript:alert(1)", allowed=True), "http"),
        (Feed("Denied", "https://www.linkedin.com/jobs/feed", allowed=True), "prohibits"),
        (Feed("", "https://jobs.example.org/feed.xml", allowed=True), "name"),
    ],
)
def test_invalid_feeds_are_rejected(bad, message):
    with pytest.raises(ValueError, match=message):
        save_feeds([bad])
    assert load_feeds() == []


def test_feeds_api_round_trip_and_validation():
    from kestrel.main import create_app

    with TestClient(create_app(), base_url="http://localhost") as c:
        ok = {"name": "Example Jobs", "url": "https://jobs.example.org/feed.xml", "allowed": True}
        assert c.put("/api/feeds", json=[ok, ok]).json() == [ok]  # duplicates collapse
        assert c.get("/api/feeds").json() == [ok]
        assert c.get("/api/setup-status").json()["steps"]["sources"] is True
        r = c.put("/api/feeds", json=[{**ok, "allowed": False}])
        assert r.status_code == 422 and "confirm" in r.json()["detail"]


async def test_feed_fetch_respects_robots_and_six_hour_spacing(no_sleep):
    save_feeds([feed(), feed("Blocked", "https://blocked.example.net/jobs.rss")])
    source = FeedSource()
    assert source.enabled()
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://jobs.example.org/robots.txt").respond(200, text="User-agent: *\nAllow: /\n")
        mock.get("https://blocked.example.net/robots.txt").respond(200, text="User-agent: *\nDisallow: /\n")
        route = mock.get("https://jobs.example.org/feed.xml").respond(200, content=RSS)
        blocked = mock.get("https://blocked.example.net/jobs.rss").respond(200, content=RSS)
        async with PoliteClient() as http:
            postings = await source.fetch(http, SearchQuery())
            assert [p.title for p in postings] == ["Backend Engineer"]
            assert any("robots.txt" in p for p in source.problems)
            assert await source.fetch(http, SearchQuery()) == []  # read again only after 6 hours
        assert route.call_count == 1
        assert blocked.call_count == 0
