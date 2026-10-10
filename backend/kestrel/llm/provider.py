"""Minimal chat client for any model the user chooses, hosted or local.

Two request formats are supported:
- `/v1/chat/completions`, which nearly every hosted provider, aggregator (OpenRouter…) and local
  server (Ollama, LM Studio, llama.cpp, vLLM…) accepts;
- the Anthropic Messages API (`/v1/messages`), picked automatically for api.anthropic.com.

Users choose the endpoint and model in Settings.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import httpx

from kestrel.config import get_settings, normalize_base_url

ApiFormat = Literal["chat_completions", "anthropic"]
__all__ = ["ChatClient", "LLMConfig", "LLMError", "LLMNotConfigured", "list_models", "normalize_base_url", "parse_json"]
ANTHROPIC_VERSION = "2023-06-01"
RETRY_STATUSES = (429, 500, 502, 503, 504, 529)
# Dropped connections worth one more try (busy local servers close sockets under load).
TRANSIENT_ERRORS = (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)
MAX_ATTEMPTS = 4


class LLMNotConfigured(Exception):
    pass


class LLMError(Exception):
    pass


def detect_format(base_url: str, configured: str = "auto") -> ApiFormat:
    if configured in ("chat_completions", "anthropic"):
        return configured  # type: ignore[return-value]
    host = (urlparse(base_url).hostname or "").lower()
    return "anthropic" if host == "api.anthropic.com" else "chat_completions"


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    api_format: ApiFormat = "chat_completions"

    def __post_init__(self):
        self.base_url = normalize_base_url(self.base_url)
        self.model = (self.model or "").strip()
        self.api_key = (self.api_key or "").strip()

    @classmethod
    def make(cls, base_url: str, api_key: str, model: str, api_format: str = "auto") -> LLMConfig:
        base = normalize_base_url(base_url)
        return cls(base, api_key, model, detect_format(base, api_format))

    @classmethod
    def for_drafting(cls) -> LLMConfig:
        s = get_settings()
        return cls.make(s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_api_format)

    @classmethod
    def for_classification(cls) -> LLMConfig:
        """Each classifier field overrides the drafting one on its own; blanks fall back."""
        s = get_settings()
        separate_endpoint = bool(s.classifier_llm_base_url.strip())
        base = s.classifier_llm_base_url if separate_endpoint else s.llm_base_url
        # A different endpoint must not inherit the drafting provider's key.
        key = s.classifier_llm_api_key or ("" if separate_endpoint else s.llm_api_key)
        fmt = s.classifier_llm_api_format if separate_endpoint else s.llm_api_format
        return cls.make(base, key, s.classifier_llm_model or s.llm_model, fmt)


def _text_of(content) -> str:
    """Message content may be a string or a list of typed parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type", "text") == "text"
        )
    return ""


def _merge_system(messages: list[dict]) -> list[dict]:
    """For chat templates without a system role: fold system text into the first user turn."""
    system = "\n\n".join(_text_of(m["content"]) for m in messages if m["role"] == "system")
    rest = [dict(m) for m in messages if m["role"] != "system"]
    if system:
        if rest and rest[0]["role"] == "user":
            rest[0]["content"] = f"{system}\n\n{_text_of(rest[0]['content'])}"
        else:
            rest.insert(0, {"role": "user", "content": system})
    return rest


# (endpoint, model) -> request fixes that server needed, learned at runtime.
_SERVER_QUIRKS: dict[tuple[str, str], set[str]] = {}


def _choose_fix(body: dict, error: str, adapted: set[str]) -> str | None:
    """Picks the adjustment for a server that rejected an optional feature. Each fix is tried once."""
    if (
        "response_format" in body
        and "response_format" not in adapted
        and ("response_format" in error or "json" in error)
    ):
        return "response_format"
    if "system" not in adapted and "system" in error and "role" in error:
        return "system"
    if "max_tokens" not in adapted and "max_tokens" in error and "max_completion_tokens" in error:
        return "max_tokens"
    if "temperature" not in adapted and "temperature" in error and "temperature" in body:
        return "temperature"
    if "response_format" in body and "response_format" not in adapted:
        # Unknown 400 with JSON mode on: JSON mode is the most common unsupported option.
        return "response_format"
    return None


def _apply_fix(body: dict, fix: str) -> None:
    if fix == "response_format":
        body.pop("response_format", None)
    elif fix == "system":
        body["messages"] = _merge_system(body["messages"])
    elif fix == "max_tokens" and "max_tokens" in body:
        body["max_completion_tokens"] = body.pop("max_tokens")
    elif fix == "temperature":
        body.pop("temperature", None)


async def _post(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    for attempt in range(MAX_ATTEMPTS):
        try:
            return await client.post(url, **kw)
        except TRANSIENT_ERRORS:
            if attempt == MAX_ATTEMPTS - 1:
                raise
            await asyncio.sleep(2**attempt)
    raise AssertionError("unreachable")


class ChatClient:
    def __init__(self, config: LLMConfig, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 120.0):
        if not config.base_url or not config.model:
            raise LLMNotConfigured("Set a model endpoint and model name in Settings first.")
        self.config = config
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    async def chat(
        self, messages: list[dict], *, json_mode: bool = False, temperature: float = 0.2, max_tokens: int = 2000
    ) -> str:
        if self.config.api_format == "anthropic":
            return await self._anthropic(messages, temperature=temperature, max_tokens=max_tokens)
        return await self._chat_completions(
            messages, json_mode=json_mode, temperature=temperature, max_tokens=max_tokens
        )

    async def _chat_completions(
        self, messages: list[dict], *, json_mode: bool, temperature: float, max_tokens: int
    ) -> str:
        url = self.config.base_url + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        body: dict = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        # Fixes this server is known to need are applied up front, so each request costs one call.
        known = _SERVER_QUIRKS.get((url, self.config.model), set())
        for fix in known:
            _apply_fix(body, fix)
        adapted: set[str] = set(known)
        async with self._client() as client:
            attempt = 0
            while True:
                resp = await _post(client, url, headers=headers, json=body)
                if resp.status_code == 400:
                    fix = _choose_fix(body, resp.text.lower(), adapted)
                    if fix:  # server quirk handled; resend once with the adjusted request
                        _apply_fix(body, fix)
                        adapted.add(fix)
                        continue
                if resp.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(2**attempt)
                    attempt += 1
                    continue
                if resp.status_code >= 400:
                    raise LLMError(f"Model endpoint returned {resp.status_code}: {resp.text[:300]}")
                data = _json(resp)
                try:
                    message = data["choices"][0]["message"]
                except (KeyError, IndexError, TypeError) as e:
                    raise LLMError(f"Unexpected response shape: {str(data)[:300]}") from e
                if adapted - known:
                    _SERVER_QUIRKS[(url, self.config.model)] = known | adapted
                return _text_of(message.get("content")) or _text_of(message.get("reasoning_content"))

    async def _anthropic(self, messages: list[dict], *, temperature: float, max_tokens: int) -> str:
        url = self.config.base_url + "/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        system = "\n\n".join(_text_of(m["content"]) for m in messages if m["role"] == "system")
        turns = [{"role": m["role"], "content": _text_of(m["content"])} for m in messages if m["role"] != "system"]
        body: dict = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": turns,
        }
        if system:
            body["system"] = system
        async with self._client() as client:
            for attempt in range(MAX_ATTEMPTS):
                resp = await _post(client, url, headers=headers, json=body)
                if resp.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(2**attempt)
                    continue
                if resp.status_code >= 400:
                    raise LLMError(f"Model endpoint returned {resp.status_code}: {resp.text[:300]}")
                data = _json(resp)
                blocks = data.get("content") if isinstance(data, dict) else None
                if not isinstance(blocks, list):
                    raise LLMError(f"Unexpected response shape: {str(data)[:300]}")
                return _text_of(blocks)
        raise LLMError("Model endpoint kept failing")

    async def chat_json(self, messages: list[dict], **kw) -> dict | list:
        text = await self.chat(messages, json_mode=True, **kw)
        return parse_json(text)


def _json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError as e:
        raise LLMError(f"The endpoint did not return JSON. Is the base URL right? {resp.text[:200]}") from e


async def list_models(config: LLMConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> list[str]:
    """Model ids the endpoint offers (GET {base}/models), so users can pick instead of typing."""
    if not config.base_url:
        raise LLMNotConfigured("Set a base URL first.")
    headers: dict[str, str] = {}
    if config.api_format == "anthropic":
        headers = {"x-api-key": config.api_key, "anthropic-version": ANTHROPIC_VERSION}
    elif config.api_key:
        headers = {"Authorization": f"Bearer {config.api_key}"}
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        resp = await client.get(
            config.base_url + "/models",
            headers=headers,
            params={"limit": 1000} if config.api_format == "anthropic" else None,
        )
    if resp.status_code >= 400:
        raise LLMError(f"Model endpoint returned {resp.status_code}: {resp.text[:300]}")
    data = _json(resp)
    items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
    ids = []
    for item in items if isinstance(items, list) else []:
        ident = item.get("id") or item.get("name") if isinstance(item, dict) else item
        if isinstance(ident, str) and ident:
            ids.append(ident)
    return sorted(set(ids))


def parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Local models sometimes wrap JSON in prose (or think out loud first).
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"Model did not return valid JSON: {text[:200]}")
