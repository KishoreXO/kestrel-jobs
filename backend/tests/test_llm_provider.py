"""The model client: custom endpoints, server quirks, and the Anthropic Messages API."""

from __future__ import annotations

import json

import httpx
import pytest

from kestrel.config import update_settings
from kestrel.llm.provider import (
    ChatClient,
    LLMConfig,
    LLMError,
    LLMNotConfigured,
    detect_format,
    list_models,
    normalize_base_url,
)

SYSTEM_AND_USER = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Hi"}]


def _ok(text="OK"):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


class Recorder:
    """A mock transport that records request bodies and answers with the given handler."""

    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request):
            self.requests.append(request)
            return self.handler(request)

        return httpx.MockTransport(handle)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:11434/v1", "http://localhost:11434/v1"),
        ("  http://localhost:11434/v1/  ", "http://localhost:11434/v1"),
        ("http://localhost:1234/v1/chat/completions", "http://localhost:1234/v1"),
        ("https://api.anthropic.com/v1/messages", "https://api.anthropic.com/v1"),
        ("", ""),
        ("localhost:1234/v1", "http://localhost:1234/v1"),
        ("openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),
    ],
)
def test_normalize_base_url(raw, expected):
    assert normalize_base_url(raw) == expected


def test_detect_format():
    assert detect_format("https://api.anthropic.com/v1") == "anthropic"
    assert detect_format("http://localhost:1234/v1") == "chat_completions"
    assert detect_format("https://proxy.example/v1", "anthropic") == "anthropic"
    assert detect_format("https://api.anthropic.com/v1", "chat_completions") == "chat_completions"


def test_not_configured():
    with pytest.raises(LLMNotConfigured):
        ChatClient(LLMConfig.make("", "", "m"))
    with pytest.raises(LLMNotConfigured):
        ChatClient(LLMConfig.make("http://x/v1", "", " "))


async def test_custom_endpoint_basic_request():
    rec = Recorder(lambda r: _ok("hello"))
    client = ChatClient(LLMConfig.make("http://my-server:9000/v1/", "", "my-model"), transport=rec.transport())
    assert await client.chat(SYSTEM_AND_USER) == "hello"
    req = rec.requests[0]
    assert str(req.url) == "http://my-server:9000/v1/chat/completions"
    assert "authorization" not in req.headers  # no key, no auth header
    assert rec.bodies[0]["model"] == "my-model"


async def test_key_sent_as_bearer():
    rec = Recorder(lambda r: _ok())
    await ChatClient(LLMConfig.make("http://x/v1", "sk-1", "m"), transport=rec.transport()).chat(SYSTEM_AND_USER)
    assert rec.requests[0].headers["authorization"] == "Bearer sk-1"


async def test_system_role_unsupported_is_merged():
    def handler(request):
        if any(m["role"] == "system" for m in json.loads(request.content)["messages"]):
            return httpx.Response(400, json={"error": "System role not supported"})
        return _ok()

    rec = Recorder(handler)
    assert (
        await ChatClient(LLMConfig.make("http://x/v1", "", "gemma"), transport=rec.transport()).chat(SYSTEM_AND_USER)
        == "OK"
    )
    assert rec.bodies[-1]["messages"] == [{"role": "user", "content": "Be brief.\n\nHi"}]


async def test_max_tokens_and_temperature_quirks():
    def handler(request):
        body = json.loads(request.content)
        if "max_tokens" in body:
            return httpx.Response(
                400,
                json={
                    "error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."}
                },
            )
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported value: 'temperature'"}})
        return _ok()

    rec = Recorder(handler)
    await ChatClient(LLMConfig.make("http://x/v1", "", "m"), transport=rec.transport()).chat(SYSTEM_AND_USER)
    assert "max_completion_tokens" in rec.bodies[-1]
    assert "temperature" not in rec.bodies[-1]


async def test_json_mode_dropped_when_unsupported():
    def handler(request):
        if "response_format" in json.loads(request.content):
            return httpx.Response(400, text="response_format is not supported")
        return _ok('{"a": 1}')

    client = ChatClient(LLMConfig.make("http://x/v1", "", "m"), transport=Recorder(handler).transport())
    assert await client.chat_json(SYSTEM_AND_USER) == {"a": 1}


async def test_unfixable_400_raises_once_adapted():
    rec = Recorder(lambda r: httpx.Response(400, text="model 'nope' not found"))
    with pytest.raises(LLMError, match="400"):
        await ChatClient(LLMConfig.make("http://x/v1", "", "nope"), transport=rec.transport()).chat(SYSTEM_AND_USER)
    assert len(rec.requests) <= 2


async def test_content_parts_and_reasoning_fallback():
    parts = httpx.Response(
        200,
        json={"choices": [{"message": {"content": [{"type": "text", "text": "Hel"}, {"type": "text", "text": "lo"}]}}]},
    )
    client = ChatClient(LLMConfig.make("http://x/v1", "", "m"), transport=httpx.MockTransport(lambda r: parts))
    assert await client.chat(SYSTEM_AND_USER) == "Hello"
    reasoning = httpx.Response(200, json={"choices": [{"message": {"content": None, "reasoning_content": "Hi"}}]})
    client = ChatClient(LLMConfig.make("http://x/v1", "", "m"), transport=httpx.MockTransport(lambda r: reasoning))
    assert await client.chat(SYSTEM_AND_USER) == "Hi"


async def test_html_response_gives_clear_error():
    page = httpx.Response(200, text="<html>Welcome</html>")
    client = ChatClient(LLMConfig.make("http://x", "", "m"), transport=httpx.MockTransport(lambda r: page))
    with pytest.raises(LLMError, match="did not return JSON"):
        await client.chat(SYSTEM_AND_USER)


# ------------------------------------------------------------------ Anthropic Messages API
def _anthropic_reply(text):
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
        },
    )


async def test_anthropic_request_format():
    rec = Recorder(lambda r: _anthropic_reply('{"ok": true}'))
    config = LLMConfig.make("https://api.anthropic.com/v1", "ak-test", "some-model")
    assert config.api_format == "anthropic"
    data = await ChatClient(config, transport=rec.transport()).chat_json(SYSTEM_AND_USER, max_tokens=50)
    assert data == {"ok": True}
    req = rec.requests[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "ak-test"
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in req.headers
    body = rec.bodies[0]
    assert body["system"] == "Be brief."
    assert body["messages"] == [{"role": "user", "content": "Hi"}]
    assert body["max_tokens"] == 50 and body["model"] == "some-model"
    assert "response_format" not in body


async def test_anthropic_errors_and_retries(monkeypatch):
    monkeypatch.setattr("kestrel.llm.provider.asyncio.sleep", _no_sleep)
    calls = iter([httpx.Response(529, json={"type": "error"}), _anthropic_reply("fine")])
    config = LLMConfig.make("https://api.anthropic.com/v1", "k", "m")
    client = ChatClient(config, transport=httpx.MockTransport(lambda r: next(calls)))
    assert await client.chat(SYSTEM_AND_USER) == "fine"
    bad = httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error"}})
    with pytest.raises(LLMError, match="401"):
        await ChatClient(config, transport=httpx.MockTransport(lambda r: bad)).chat(SYSTEM_AND_USER)


async def _no_sleep(_seconds):
    return None


async def test_list_models_both_formats():
    openai_style = httpx.Response(200, json={"data": [{"id": "b-model"}, {"id": "a-model"}]})
    models = await list_models(
        LLMConfig.make("http://x/v1", "", ""), transport=httpx.MockTransport(lambda r: openai_style)
    )
    assert models == ["a-model", "b-model"]

    rec = Recorder(lambda r: httpx.Response(200, json={"data": [{"id": "m-1", "type": "model"}]}))
    models = await list_models(LLMConfig.make("https://api.anthropic.com/v1", "ak", ""), transport=rec.transport())
    assert models == ["m-1"]
    assert rec.requests[0].headers["x-api-key"] == "ak"


# ------------------------------------------------------------------ settings → config
def test_classifier_fields_fall_back_independently():
    update_settings({"llm_base_url": "http://draft/v1", "llm_api_key": "draft-key", "llm_model": "draft-model"})
    c = LLMConfig.for_classification()
    assert (c.base_url, c.api_key, c.model) == ("http://draft/v1", "draft-key", "draft-model")

    update_settings({"classifier_llm_model": "small-model"})
    c = LLMConfig.for_classification()
    assert (c.base_url, c.api_key, c.model) == ("http://draft/v1", "draft-key", "small-model")

    # A separate endpoint never inherits the drafting provider's key.
    update_settings({"classifier_llm_base_url": "https://api.anthropic.com/v1"})
    c = LLMConfig.for_classification()
    assert c.api_key == "" and c.api_format == "anthropic"


def test_secret_can_be_cleared():
    update_settings({"llm_api_key": "old-provider-key"})
    assert LLMConfig.for_drafting().api_key == "old-provider-key"
    update_settings({"llm_api_key": ""})  # blank keeps it
    assert LLMConfig.for_drafting().api_key == "old-provider-key"
    update_settings({"llm_api_key": "__clear__", "llm_base_url": "http://localhost:1234/v1"})
    assert LLMConfig.for_drafting().api_key == ""


async def test_learned_quirks_skip_failed_requests():
    from kestrel.llm import provider

    provider._SERVER_QUIRKS.clear()

    def handler(request):
        if any(m["role"] == "system" for m in json.loads(request.content)["messages"]):
            return httpx.Response(400, json={"error": "System role not supported"})
        return _ok("{}")

    rec = Recorder(handler)
    client = ChatClient(LLMConfig.make("http://quirky/v1", "", "gemma"), transport=rec.transport())
    await client.chat(SYSTEM_AND_USER)
    assert len(rec.requests) == 2
    await client.chat(SYSTEM_AND_USER)
    assert len(rec.requests) == 3  # the second call goes straight to the working format
    provider._SERVER_QUIRKS.clear()


async def test_dropped_connection_is_retried(monkeypatch):
    monkeypatch.setattr("kestrel.llm.provider.asyncio.sleep", _no_sleep)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadError("connection reset")
        return _ok("back")

    client = ChatClient(LLMConfig.make("http://x/v1", "", "m"), transport=httpx.MockTransport(handler))
    assert await client.chat(SYSTEM_AND_USER) == "back"


async def test_network_errors_become_classifier_errors():
    from kestrel.classify.base import ClassifierError, Noul
    from kestrel.classify.llm_backend import LLMClassifier

    def handler(request):
        raise httpx.ConnectError("refused")

    clf = LLMClassifier(config=LLMConfig.make("http://x/v1", "", "m"), transport=httpx.MockTransport(handler))
    with pytest.raises(ClassifierError, match="Couldn't reach"):
        await clf.ask("state", {"q": Noul("x")})
