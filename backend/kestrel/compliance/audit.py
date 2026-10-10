from __future__ import annotations

from kestrel.compliance.denylist import domain_of
from kestrel.db import session_scope
from kestrel.models import AuditLog


def record(url: str, *, purpose: str, method: str = "GET", status: int | None = None, blocked_reason: str = "") -> None:
    """Append an entry to the local audit log. Query strings are dropped so keys never get logged."""
    safe_url = url.split("?", 1)[0]
    with session_scope() as session:
        session.add(
            AuditLog(
                method=method,
                url=safe_url,
                domain=domain_of(url),
                purpose=purpose,
                status=status,
                blocked_reason=blocked_reason,
            )
        )
        session.commit()
