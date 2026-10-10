"""The user's list of companies whose public job boards we read.

Stored in `<DATA_DIR>/companies.yaml`:

    - name: Example Inc
      ats: greenhouse        # greenhouse | lever | ashby
      slug: exampleinc       # board token / site name from the company's careers URL
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from kestrel.config import get_settings

ATS_TYPES = ("greenhouse", "lever", "ashby")


# Board tokens are plain identifiers; anything else could change the API URL they are put into.
SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


@dataclass
class Company:
    name: str
    ats: str
    slug: str
    region: str = ""  # Lever only: "eu" for api.eu.lever.co

    def validate(self) -> None:
        if self.ats not in ATS_TYPES:
            raise ValueError(f"Unknown ATS '{self.ats}' for {self.name}; use one of {ATS_TYPES}")
        if not SLUG_RE.fullmatch(self.slug or "") or ".." in self.slug:
            raise ValueError(f"Invalid board slug for {self.name}: {self.slug!r}")


def load_companies() -> list[Company]:
    path = get_settings().companies_file
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as e:
        raise ValueError(f"the file isn't valid YAML ({e})") from e
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError("expected a list of entries")
    companies = []
    for item in raw:
        c = Company(
            name=str(item.get("name", "")).strip() or str(item.get("slug", "")),
            ats=str(item.get("ats", "")).strip().lower(),
            slug=str(item.get("slug", "")).strip(),
            region=str(item.get("region", "")).strip().lower(),
        )
        c.validate()
        companies.append(c)
    return companies


def save_companies(companies: list[Company]) -> None:
    for c in companies:
        c.validate()
    seen: set[tuple[str, str]] = set()
    unique = []
    for c in companies:
        key = (c.ats, c.slug.lower())
        if key not in seen:
            seen.add(key)
            unique.append(c)
    data = [{k: v for k, v in asdict(c).items() if v} for c in unique]
    get_settings().companies_file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def add_company(company: Company) -> bool:
    companies = load_companies()
    if any(c.ats == company.ats and c.slug.lower() == company.slug.lower() for c in companies):
        return False
    companies.append(company)
    save_companies(companies)
    return True


STARTER_FILE = Path(__file__).resolve().parents[1] / "starter_companies.yaml"


def load_starter() -> dict[str, list[Company]]:
    """Curated public boards grouped by field, offered in the dashboard. Never added automatically."""
    raw = yaml.safe_load(STARTER_FILE.read_text(encoding="utf-8")) or {}
    return {group: [Company(**item) for item in items] for group, items in raw.items()}
