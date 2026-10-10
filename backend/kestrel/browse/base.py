"""Read-only browser backend interface.

By design the interface has no click, type, fill or submit operations: a backend can only load
a URL and read what is on the page. Following a link means navigating to its URL, which goes
through the full policy gate (see policy.py) again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Link:
    text: str
    href: str


@dataclass
class PageSnapshot:
    url: str
    title: str = ""
    text: str = ""
    links: list[Link] = field(default_factory=list)
    status: int | None = None


class BrowserBackend(Protocol):
    name: str

    async def start(self) -> None: ...

    async def load(self, url: str) -> PageSnapshot: ...

    async def read_more(self) -> PageSnapshot:
        """Scroll to reveal more of the current page and return a fresh snapshot."""
        ...

    async def close(self) -> None: ...


def get_backend(name: str, user_agent: str) -> BrowserBackend:
    if name == "cua":
        from kestrel.browse.cua import CuaBackend

        return CuaBackend()
    from kestrel.browse.playwright_backend import PlaywrightBackend

    return PlaywrightBackend(user_agent=user_agent)
