from __future__ import annotations

from kestrel import PROJECT_URL, __version__
from kestrel.config import get_settings

ROBOTS_TOKEN = "Kestrel"  # noqa: S105 - the robots.txt user-agent name, not a secret


def user_agent() -> str:
    """An honest User-Agent so operators can see who we are and reach the person running the bot."""
    contact = get_settings().contact_email
    suffix = f"; contact: {contact}" if contact else ""
    return f"{ROBOTS_TOKEN}/{__version__} (+{PROJECT_URL}{suffix})"
