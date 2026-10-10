"""Job feeds the user adds themselves: RSS 2.0, Atom or JSON Feed.

Stored in `<DATA_DIR>/feeds.yaml`:

    - name: Example Jobs
      url: https://jobs.example.org/feed.xml
      allowed: true          # the user confirmed the site permits automated reading of this feed

Feeds are read-only, fetched at most every 6 hours, only where robots.txt allows it, and never
from a site on the denylist. Postings link back to the feed's site.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from xml.etree.ElementTree import Element, ParseError

import httpx
import yaml
from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from kestrel import PROJECT_URL
from kestrel.compliance import robots
from kestrel.compliance.denylist import PolicyViolation, domain_of, is_denied
from kestrel.compliance.http import PoliteClient, SourceBackoff
from kestrel.config import get_settings
from kestrel.sources.base import (
    JobPosting,
    SearchQuery,
    SourcePolicy,
    html_to_text,
    http_url,
    mark_fetched,
    parse_dt,
    recently_fetched,
)

log = logging.getLogger(__name__)

FEED_CACHE = timedelta(hours=6)
MAX_ITEMS = 200
MAX_BYTES = 1_000_000
ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT_ENCODED = "{http://purl.org/rss/1.0/modules/content/}encoded"
ACCEPT = "application/feed+json, application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"


@dataclass
class Feed:
    name: str
    url: str
    allowed: bool = False

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("Every feed needs a name.")
        if not http_url(self.url) or not urlsplit(self.url.strip()).hostname:
            raise ValueError(f"{self.name}: the feed address must start with http:// or https://")
        if is_denied(self.url):
            raise ValueError(
                f"{self.name}: {domain_of(self.url)} prohibits automated access in its terms, so Kestrel can't read it."
            )
        if not self.allowed:
            raise ValueError(f"{self.name}: confirm that the site allows automated reading of this feed.")

    @property
    def key(self) -> str:
        return hashlib.sha1(self.url.encode(), usedforsecurity=False).hexdigest()[:16]  # an id, not security


def load_feeds() -> list[Feed]:
    path = get_settings().feeds_file
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as e:
        raise ValueError(f"the file isn't valid YAML ({e})") from e
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError("expected a list of entries")
    feeds = []
    for item in raw:
        feed = Feed(
            name=str(item.get("name", "")).strip(),
            url=str(item.get("url", "")).strip(),
            allowed=item.get("allowed") is True,
        )
        feed.validate()
        feeds.append(feed)
    return feeds


def save_feeds(feeds: list[Feed]) -> None:
    unique: dict[str, Feed] = {}
    for feed in feeds:
        feed.name, feed.url = feed.name.strip(), feed.url.strip()
        feed.validate()
        unique.setdefault(feed.url.lower(), feed)
    data = [asdict(f) for f in unique.values()]
    get_settings().feeds_file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------- parsing
def _text(el: Element | None, path: str) -> str:
    found = el.find(path) if el is not None else None
    return (found.text or "").strip() if found is not None else ""


def _rfc822(value: str) -> datetime | None:
    try:
        return parse_dt(parsedate_to_datetime(value).isoformat()) if value else None
    except (TypeError, ValueError):
        return parse_dt(value)


@dataclass
class _Item:
    ident: str
    title: str
    link: str
    body: str
    posted: datetime | None


def _posting(feed: Feed, site: str, item: _Item) -> JobPosting | None:
    url = http_url(urljoin(feed.url, item.link)) if item.link else ""
    if not url or not item.title:
        return None
    return JobPosting(
        source="feed",
        external_id=f"{feed.key}:{item.ident or url}"[:300],
        company=feed.name,
        title=html_to_text(item.title)[:300],
        url=url,
        apply_url=url,
        description=html_to_text(item.body),
        posted_at=item.posted,
        attribution_text=f"via {feed.name}",
        attribution_url=http_url(site) or url,
        board=feed.key,
    )


def _parse_json(feed: Feed, data: dict) -> list[JobPosting]:
    site = str(data.get("home_page_url") or "")
    out = []
    for item in (data.get("items") or [])[:MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        posting = _posting(
            feed,
            site,
            _Item(
                ident=str(item.get("id") or ""),
                title=str(item.get("title") or ""),
                link=str(item.get("url") or item.get("external_url") or ""),
                body=str(item.get("content_html") or item.get("content_text") or item.get("summary") or ""),
                posted=parse_dt(item.get("date_published") or item.get("date_modified")),
            ),
        )
        if posting:
            out.append(posting)
    return out


def _atom_link(el: Element) -> str:
    for link in el.findall(f"{ATOM}link"):
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href", "")
    return ""


def _parse_xml(feed: Feed, root: Element) -> list[JobPosting]:
    out = []
    if root.tag == f"{ATOM}feed":
        site = _atom_link(root)
        for entry in root.findall(f"{ATOM}entry")[:MAX_ITEMS]:
            posting = _posting(
                feed,
                site,
                _Item(
                    ident=_text(entry, f"{ATOM}id"),
                    title=_text(entry, f"{ATOM}title"),
                    link=_atom_link(entry),
                    body=_text(entry, f"{ATOM}content") or _text(entry, f"{ATOM}summary"),
                    posted=parse_dt(_text(entry, f"{ATOM}published") or _text(entry, f"{ATOM}updated")),
                ),
            )
            if posting:
                out.append(posting)
        return out
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ValueError("not an RSS, Atom or JSON feed")
    site = _text(channel, "link")
    for item in channel.findall("item")[:MAX_ITEMS]:
        posting = _posting(
            feed,
            site,
            _Item(
                ident=_text(item, "guid"),
                title=_text(item, "title"),
                link=_text(item, "link") or (_text(item, "guid") if http_url(_text(item, "guid")) else ""),
                body=_text(item, CONTENT_ENCODED) or _text(item, "description"),
                posted=_rfc822(_text(item, "pubDate")),
            ),
        )
        if posting:
            out.append(posting)
    return out


def parse_feed(feed: Feed, body: bytes, content_type: str = "") -> list[JobPosting]:
    """Parses a feed. XML goes through defusedxml, which refuses entity tricks and external references."""
    if len(body) > MAX_BYTES:
        raise ValueError("the feed is larger than 1 MB")
    if "json" in content_type or body.lstrip()[:1] == b"{":
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("not a JSON Feed")
        return _parse_json(feed, data)
    try:
        root = SafeET.fromstring(body, forbid_dtd=True)
    except DefusedXmlException as e:
        raise ValueError("the feed uses XML features Kestrel refuses for safety") from e
    except ParseError as e:
        raise ValueError(f"the feed isn't valid XML ({e})") from e
    return _parse_xml(feed, root)


# ---------------------------------------------------------------- source
class FeedSource:
    policy = SourcePolicy(
        name="feeds",
        terms_url=f"{PROJECT_URL}/blob/main/docs/SOURCES.md#custom-feeds",
        min_interval_seconds=10.0,
        max_requests_per_scan=20,
    )

    def __init__(self) -> None:
        self.problems: list[str] = []  # shown in the scan log; one broken feed never stops the others

    def enabled(self) -> bool:
        try:
            return bool(load_feeds())
        except ValueError:
            return True  # a hand-edited feeds.yaml with a bad entry: the scan log explains it

    async def fetch(self, http: PoliteClient, query: SearchQuery) -> list[JobPosting]:
        self.problems = []
        postings: list[JobPosting] = []
        budget = self.policy.max_requests_per_scan
        for feed in load_feeds():
            key = f"board:feeds:{feed.key}"
            if recently_fetched(key, FEED_CACHE):
                continue
            if budget <= 0:
                self.problems.append("request budget for this scan used up; the rest are read next time")
                break
            budget -= 1
            try:
                postings.extend(await self._fetch_one(http, feed))
            except (ValueError, PolicyViolation, SourceBackoff, httpx.HTTPError) as e:
                self.problems.append(f"{feed.name}: {e}")
                continue
            mark_fetched(key)
        return postings

    async def _fetch_one(self, http: PoliteClient, feed: Feed) -> list[JobPosting]:
        if not await robots.can_fetch(feed.url):
            raise PolicyViolation("robots.txt doesn't allow reading this feed, so it was skipped")
        interval = max(self.policy.min_interval_seconds, robots.crawl_delay(feed.url) or 0)
        resp = await http.get(feed.url, purpose=f"feed {feed.name}", min_interval=interval, headers={"Accept": ACCEPT})
        if resp.status_code == 404:
            raise ValueError("the feed address returned 404 (not found)")
        resp.raise_for_status()
        return parse_feed(feed, resp.content, resp.headers.get("content-type", ""))
