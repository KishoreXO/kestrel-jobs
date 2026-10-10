from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from kestrel.compliance.http import PoliteClient


@dataclass(frozen=True)
class SourcePolicy:
    """Each source declares the rules it must follow. See docs/SOURCES.md."""

    name: str
    terms_url: str
    min_interval_seconds: float
    attribution_text: str = ""
    attribution_url: str = ""
    # Maximum API calls per scan (keeps us well under published quotas).
    max_requests_per_scan: int = 50


@dataclass
class SearchQuery:
    keywords: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    remote_ok: bool = True


@dataclass
class JobPosting:
    source: str
    external_id: str
    company: str
    title: str
    url: str
    apply_url: str = ""
    location: str = ""
    remote: bool | None = None
    description: str = ""
    salary: str = ""
    department: str = ""
    posted_at: datetime | None = None
    attribution_text: str = ""
    attribution_url: str = ""
    board: str = ""

    def content_hash(self) -> str:
        blob = "\x1f".join([self.title, self.company, self.location, self.description, self.salary])
        return hashlib.sha256(blob.encode()).hexdigest()

    def dedupe_key(self) -> str:
        norm = lambda s: re.sub(r"\W+", " ", s.lower()).strip()  # noqa: E731
        return hashlib.sha1(  # an id, not security
            f"{norm(self.company)}|{norm(self.title)}|{norm(self.location)}".encode(), usedforsecurity=False
        ).hexdigest()


class SourceAdapter(Protocol):
    policy: SourcePolicy

    def enabled(self) -> bool: ...

    async def fetch(self, http: PoliteClient, query: SearchQuery) -> list[JobPosting]: ...


def http_url(url: str) -> str:
    """The URL if it is http(s), otherwise "". Posting links come from third parties and are shown as links."""
    value = (url or "").strip()
    return value if value.lower().startswith(("https://", "http://")) else ""


_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr)[^>]*>", re.I)


def html_to_text(raw: str) -> str:
    """Plain-text rendering used for classification. The original posting is always linked."""
    if not raw:
        return ""
    text = html.unescape(raw)  # Greenhouse double-encodes its HTML.
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def recently_fetched(key: str, within: timedelta) -> bool:
    from kestrel.db import session_scope
    from kestrel.models import FetchState

    with session_scope() as session:
        state = session.get(FetchState, key)
        if not state or not state.last_fetched:
            return False
        last = state.last_fetched if state.last_fetched.tzinfo else state.last_fetched.replace(tzinfo=UTC)
        return datetime.now(UTC) - last < within


def mark_fetched(key: str) -> None:
    from kestrel.db import session_scope
    from kestrel.models import FetchState

    with session_scope() as session:
        state = session.get(FetchState, key) or FetchState(key=key)
        state.last_fetched = datetime.now(UTC)
        session.add(state)
        session.commit()


def clear_marks(source_name: str) -> None:
    """Forget fetch timestamps for a source so a failed scan doesn't delay the next attempt."""
    from sqlmodel import col, delete

    from kestrel.db import session_scope
    from kestrel.models import FetchState

    with session_scope() as session:
        for prefix in (f"board:{source_name}:", f"search:{source_name}"):
            session.exec(delete(FetchState).where(col(FetchState.key).startswith(prefix)))
        session.commit()


def parse_dt(value) -> datetime | None:
    """Parses ISO strings or epoch seconds/milliseconds into an aware UTC datetime."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=UTC)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, OSError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
