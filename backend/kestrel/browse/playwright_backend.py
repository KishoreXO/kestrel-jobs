"""Headless Chromium backend (alternative to Cua Driver).

Runs in a fresh, empty browser context (no cookies, no logged-in sessions) and identifies
itself with Kestrel's User-Agent. Install with: uv sync --extra browse && playwright install chromium
"""

from __future__ import annotations

import contextlib
from typing import Any

from kestrel.browse.base import Link, PageSnapshot

_LINKS_JS = """() => Array.from(document.querySelectorAll('a[href]')).slice(0, 400)
  .map(a => ({text: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 160), href: a.href}))"""


class PlaywrightBackend:
    name = "playwright"

    def __init__(self, user_agent: str):
        self.user_agent = user_agent
        # Typed loosely: Playwright is an optional extra and may not be installed.
        self._pw: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._status: int | None = None

    async def start(self) -> None:
        try:
            from playwright.async_api import async_playwright  # pyright: ignore[reportMissingImports]
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "Playwright not installed: run `uv sync --extra browse` then `uv run playwright install chromium`"
            ) from e
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        context = await self._browser.new_context(
            user_agent=self.user_agent, accept_downloads=False, java_script_enabled=True
        )
        self._page = await context.new_page()

    async def _snapshot(self) -> PageSnapshot:
        page = self._page
        text = await page.inner_text("body") if await page.query_selector("body") else ""
        links = [Link(**item) for item in await page.evaluate(_LINKS_JS)]
        return PageSnapshot(url=page.url, title=await page.title(), text=text[:60000], links=links, status=self._status)

    async def load(self, url: str) -> PageSnapshot:
        response = await self._page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self._status = response.status if response else None
        with contextlib.suppress(Exception):  # busy pages never go idle; read what has loaded
            await self._page.wait_for_load_state("networkidle", timeout=8000)
        return await self._snapshot()

    async def read_more(self) -> PageSnapshot:
        await self._page.mouse.wheel(0, 4000)
        await self._page.wait_for_timeout(1500)
        return await self._snapshot()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
