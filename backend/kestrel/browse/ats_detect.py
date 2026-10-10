"""Detect careers pages hosted on an ATS with an official public API, so we can switch to it."""

from __future__ import annotations

import re

from kestrel.sources.companies import Company

_PATTERNS = [
    (
        "greenhouse",
        re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)"),
    ),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.(eu\.)?lever\.co/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)")),
]

_IGNORED = {"embed", "v1", "api", "jobs"}


def detect_ats(urls: list[str], company_name: str = "") -> Company | None:
    for url in urls:
        for ats, pattern in _PATTERNS:
            m = pattern.search(url)
            if not m:
                continue
            if ats == "lever":
                region, slug = ("eu" if m.group(1) else ""), m.group(2)
            else:
                region, slug = "", m.group(1)
            if slug.lower() in _IGNORED:
                continue
            return Company(name=company_name or slug, ats=ats, slug=slug, region=region)
    return None
