"""Cua Driver backend — the recommended browser backend.

Cua Driver (https://github.com/trycua/cua, MIT) drives a real Chromium-family browser through
typed page tools, in the background, without taking over the user's mouse or keyboard. We
launch it with an *isolated, fresh profile* (no existing cookies or logged-in sessions) and only
use its read tools: `browser_prepare`, `get_browser_state` (snapshot) and `browser_navigate`.
`browser_click`, `browser_type`, uploads and downloads are never called.

Install with: uv sync --extra browse   (and install the Cua Driver runtime: https://cua.ai/docs)

Tool names and arguments follow https://cua.ai/docs/how-to-guides/driver/drive-a-web-page.
If your installed Cua Driver version differs, adjust `_call` / the argument names below.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from kestrel.browse.base import Link, PageSnapshot

READ_ONLY_TOOLS = frozenset(
    {"browser_prepare", "get_browser_state", "browser_navigate", "browser_pointer", "end_session"}
)


class CuaBackend:
    name = "cua"

    def __init__(self) -> None:
        self._driver: Any = None  # Cua Driver is an optional extra and may not be installed
        self._session = f"kestrel-{uuid.uuid4().hex[:8]}"
        self._target_id = None
        self._tab_id = None

    async def start(self) -> None:
        try:
            from cua_driver import CuaDriver  # pyright: ignore[reportMissingImports]
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "Cua Driver is not installed: run `uv sync --extra browse` and install the "
                "Cua Driver runtime (https://cua.ai/docs)."
            ) from e
        self._driver = CuaDriver.create()
        prepared = await self._call(
            "browser_prepare",
            {
                "session": self._session,
                "allow_launch": True,
                "profile": {"mode": "isolated_new"},
            },
        )
        self._target_id = prepared.get("target_id")
        self._tab_id = prepared.get("tab_id")
        if not (self._target_id and self._tab_id):
            bound = await self._call(
                "get_browser_state",
                {"pid": prepared.get("pid"), "window_id": prepared.get("window_id"), "session": self._session},
            )
            self._target_id = bound.get("target_id")
            self._tab_id = bound.get("tab_id")

    async def _call(self, tool: str, args: dict) -> dict:
        if tool not in READ_ONLY_TOOLS:  # defence in depth
            raise PermissionError(f"Kestrel never uses the '{tool}' tool")
        result = self._driver.call_tool(tool, json.dumps(args))
        if hasattr(result, "__await__"):
            result = await result
        if isinstance(result, (str, bytes)):
            result = json.loads(result)
        if isinstance(result, dict) and result.get("is_error"):
            raise RuntimeError(f"Cua Driver {tool} failed: {result.get('error') or result}")
        if isinstance(result, dict) and isinstance(result.get("structured_content"), dict):
            return result["structured_content"]
        return result if isinstance(result, dict) else {}

    async def _snapshot(self) -> PageSnapshot:
        state = await self._call(
            "get_browser_state",
            {
                "target_id": self._target_id,
                "tab_id": self._tab_id,
                "session": self._session,
                "snapshot_format": "semantic_v2",
            },
        )
        page = state.get("page") or {}
        outline = state.get("outline") or ""
        if not isinstance(outline, str):
            outline = json.dumps(outline, ensure_ascii=False)
        links = []
        for ref in (state.get("refs") or []) + (state.get("content_refs") or []):
            href = ref.get("href") or ref.get("url")
            if href:
                links.append(Link(text=str(ref.get("name") or ref.get("label") or "")[:160], href=href))
        return PageSnapshot(
            url=page.get("url") or state.get("url", ""),
            title=page.get("title", ""),
            text=outline[:60000],
            links=links,
            status=page.get("status"),
        )

    async def load(self, url: str) -> PageSnapshot:
        await self._call(
            "browser_navigate",
            {"target_id": self._target_id, "tab_id": self._tab_id, "url": url, "session": self._session},
        )
        return await self._snapshot()

    async def read_more(self) -> PageSnapshot:
        await self._call(
            "browser_pointer",
            {
                "target_id": self._target_id,
                "tab_id": self._tab_id,
                "action": "scroll",
                "delta_y": 4000,
                "session": self._session,
            },
        )
        return await self._snapshot()

    async def close(self) -> None:
        if self._driver is None:
            return
        try:
            await self._call("end_session", {"session": self._session})
        finally:
            self._driver.shutdown()
            self._driver: Any = None  # Cua Driver is an optional extra and may not be installed
