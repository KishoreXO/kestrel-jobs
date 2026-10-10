"""Runtime configuration.

Values come from environment variables / `.env`, and can be overridden from the dashboard's
Settings page (persisted to `<DATA_DIR>/settings.json`). Secrets never leave this machine.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sent by the dashboard to delete a saved secret (a blank value means "keep the current one").
CLEAR_SECRET = "__clear__"  # noqa: S105 - a marker, not a secret

SECRET_FIELDS = {"typesafe_api_key", "llm_api_key", "classifier_llm_api_key", "usajobs_api_key", "adzuna_app_key"}

# Fields the dashboard is allowed to change at runtime.
EDITABLE_FIELDS = {
    "contact_email",
    "classifier",
    "typesafe_api_key",
    "jev_model",
    "llm_base_url",
    "llm_api_key",
    "llm_model",
    "llm_api_format",
    "classifier_llm_base_url",
    "classifier_llm_api_key",
    "classifier_llm_model",
    "classifier_llm_api_format",
    "enable_greenhouse",
    "enable_lever",
    "enable_ashby",
    "enable_usajobs",
    "usajobs_api_key",
    "usajobs_email",
    "enable_adzuna",
    "adzuna_app_id",
    "adzuna_app_key",
    "adzuna_country",
    "scan_interval_hours",
    "auto_scan",
    "prefilter",
    "browse_enabled",
    "browser",
    "browse_domains",
    "browse_max_pages_per_domain",
}


ApiFormatSetting = Literal["auto", "chat_completions", "anthropic"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8000
    frontend_origin: str = "http://localhost:3000"
    # Extra host names (comma-separated) the API answers to, besides this machine. Keep it empty
    # unless you deliberately run Kestrel behind your own reverse proxy.
    allowed_hosts: str = ""
    # Interactive API docs at /docs, for development only.
    kestrel_api_docs: bool = False

    # Identifies you to the sites/APIs we contact (sent in the User-Agent). Strongly recommended.
    contact_email: str = ""

    # --- Classification -------------------------------------------------------------------
    # Jev or another classification/decision model is recommended.
    # "jev" (Recommended): TypeSafe's Jev, a typed classification model built for exactly this.
    # "llm": another model you choose (hosted API or local) answering the same typed questions.
    classifier: Literal["jev", "llm"] = "jev"
    typesafe_api_key: str = ""
    jev_model: str = "jev-latest"
    # Optional separate endpoint for classification when classifier == "llm";
    # falls back to the answer-drafting LLM settings below when empty.
    classifier_llm_base_url: str = ""
    classifier_llm_api_key: str = ""
    classifier_llm_model: str = ""
    classifier_llm_api_format: ApiFormatSetting = "auto"

    # --- Answer drafting model: any model, hosted API or local ----------------------------------
    # Examples: a hosted provider's /v1 URL (OpenRouter, Anthropic…), or a local server such as
    # Ollama (http://localhost:11434/v1) or LM Studio (http://localhost:1234/v1).
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    # "auto" picks the request format from the address (api.anthropic.com uses the Anthropic
    # Messages API; everything else uses /v1/chat/completions). Override only for proxies.
    llm_api_format: ApiFormatSetting = "auto"

    # --- Sources (official APIs) ----------------------------------------------------------
    enable_greenhouse: bool = True
    enable_lever: bool = True
    enable_ashby: bool = True
    enable_usajobs: bool = False
    usajobs_api_key: str = ""
    usajobs_email: str = ""
    enable_adzuna: bool = False
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    adzuna_country: str = "us"

    # --- Scheduling -------------------------------------------------------------------------
    scan_interval_hours: int = Field(default=12, ge=6)
    auto_scan: bool = False
    # Cheap local keyword filter applied before any model call: "strict" | "loose" | "off".
    prefilter: Literal["strict", "loose", "off"] = "loose"

    # --- Optional read-only browsing ----------------------------------------------------------
    browse_enabled: bool = False
    # "cua" (Recommended): Cua Driver. "playwright": headless Chromium alternative.
    browser: Literal["cua", "playwright"] = "cua"
    # Only these domains may be browsed (after the denylist and robots.txt checks).
    browse_domains: list[str] = Field(default_factory=list)
    browse_max_pages_per_domain: int = Field(default=15, ge=1, le=50)

    @field_validator("scan_interval_hours")
    @classmethod
    def _min_interval(cls, v: int) -> int:
        return max(6, v)

    @field_validator("adzuna_country")
    @classmethod
    def _country_code(cls, v: str) -> str:
        # It becomes part of the Adzuna API path, so only a plain two-letter code is accepted.
        code = (v or "").strip().lower()
        if not re.fullmatch(r"[a-z]{2}", code):
            raise ValueError("Adzuna country must be a two-letter code such as us, gb or de")
        return code

    @property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kestrel.db"

    @property
    def companies_file(self) -> Path:
        return self.data_dir / "companies.yaml"

    @property
    def feeds_file(self) -> Path:
        return self.data_dir / "feeds.yaml"


LOCAL_HOST_PREFIXES = ("localhost", "127.", "[::1]", "192.168.", "10.", "host.docker.internal")


def normalize_base_url(url: str) -> str:
    """Accepts what people actually paste: trailing slashes, spaces, or the full endpoint URL."""
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        # "localhost:1234/v1" → http for this machine and the local network, https elsewhere.
        local = url.startswith(LOCAL_HOST_PREFIXES)
        url = ("http://" if local else "https://") + url
    for suffix in ("/chat/completions", "/completions", "/messages"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url


def _load_overrides(base: Settings) -> dict:
    path = base.settings_file
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if k in EDITABLE_FIELDS}


@lru_cache
def get_settings() -> Settings:
    base = Settings()
    overrides = _load_overrides(base)
    try:
        settings = Settings(**{**base.model_dump(), **overrides}) if overrides else base
    except ValidationError as e:
        # A hand-edited or outdated settings.json must not stop Kestrel starting: skip the bad values.
        bad = {str(err["loc"][0]) for err in e.errors() if err.get("loc")}
        settings = Settings(**{**base.model_dump(), **{k: v for k, v in overrides.items() if k not in bad}})
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


def update_settings(changes: dict) -> Settings:
    """Persist dashboard edits and refresh the cached settings."""
    current = get_settings()
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Not editable: {', '.join(sorted(unknown))}")
    path = current.settings_file
    existing = _load_overrides(current)
    # Empty secret values from the UI mean "keep the current secret".
    for key, value in changes.items():
        if key in SECRET_FIELDS and value in ("", None):
            continue
        if key in SECRET_FIELDS and value == CLEAR_SECRET:
            existing[key] = ""
            continue
        if isinstance(value, str):
            existing[key] = normalize_base_url(value) if key.endswith("_base_url") else value.strip()
        else:
            existing[key] = value
    Settings(**{**current.model_dump(), **existing})  # validate before writing
    _write_private(path, json.dumps(existing, indent=2))
    get_settings.cache_clear()
    return get_settings()


def _write_private(path: Path, text: str) -> None:
    """Writes the settings file readable only by you (it holds API keys). No-op permissions on Windows."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def public_settings(settings: Settings) -> dict:
    """Settings safe to send to the dashboard (secrets masked)."""
    out = {}
    for key in sorted(EDITABLE_FIELDS):
        value = getattr(settings, key)
        if key in SECRET_FIELDS:
            out[key] = "set" if value else ""
        else:
            out[key] = value
    return out
