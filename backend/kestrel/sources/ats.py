"""Public ATS job-board APIs (Greenhouse, Lever, Ashby).

These endpoints are published by the ATS vendors specifically so that third parties can list
their customers' open roles. We only read published postings, never apply through them, and
cache each board for at least 6 hours.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import httpx

from kestrel.compliance.denylist import PolicyViolation
from kestrel.compliance.http import PoliteClient, SourceBackoff
from kestrel.config import get_settings
from kestrel.sources.base import (
    JobPosting,
    SearchQuery,
    SourcePolicy,
    html_to_text,
    mark_fetched,
    parse_dt,
    recently_fetched,
)
from kestrel.sources.companies import Company, load_companies

log = logging.getLogger(__name__)

BOARD_CACHE = timedelta(hours=6)


class _ATSBase:
    policy: SourcePolicy
    ats: str

    def __init__(self) -> None:
        self.problems: list[str] = []  # shown in the scan log; one broken board never stops the others

    def enabled(self) -> bool:
        return bool(getattr(get_settings(), f"enable_{self.ats}"))

    async def fetch(self, http: PoliteClient, query: SearchQuery) -> list[JobPosting]:
        self.problems = []
        postings: list[JobPosting] = []
        companies = [c for c in load_companies() if c.ats == self.ats]
        budget = self.policy.max_requests_per_scan
        for company in companies:
            key = f"board:{self.ats}:{company.slug.lower()}"
            if recently_fetched(key, BOARD_CACHE):
                continue
            if budget <= 0:
                self.problems.append("request budget for this scan used up; the rest are read next time")
                break
            budget -= 1
            try:
                postings.extend(await self._fetch_board(http, company))
            except (SourceBackoff, PolicyViolation) as e:
                # The whole API host asked us to stop: keep what we have and leave the rest for later.
                self.problems.append(f"{company.slug}: {e}")
                break
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as e:
                self.problems.append(f"{company.slug}: failed ({type(e).__name__}: {str(e)[:200]})")
                continue  # not marked, so it is retried next scan
            mark_fetched(key)
        return postings

    async def _fetch_board(self, http: PoliteClient, company: Company) -> list[JobPosting]:
        resp = await http.get(
            self.board_url(company),
            purpose=f"{self.ats} board {company.slug}",
            min_interval=self.policy.min_interval_seconds,
            params=self.params(),
        )
        if resp.status_code == 404:
            log.warning("%s board not found: %s", self.ats, company.slug)
            return []
        resp.raise_for_status()
        return self.parse(company, resp.json())

    def board_url(self, company: Company) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def params(self) -> dict:
        return {}

    def parse(self, company: Company, data) -> list[JobPosting]:  # pragma: no cover - abstract
        raise NotImplementedError


class GreenhouseSource(_ATSBase):
    ats = "greenhouse"
    policy = SourcePolicy(
        name="greenhouse",
        terms_url="https://developers.greenhouse.io/job-board.html",
        min_interval_seconds=2.0,
    )

    def board_url(self, company: Company) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{company.slug}/jobs"

    def params(self) -> dict:
        return {"content": "true"}

    def parse(self, company: Company, data) -> list[JobPosting]:
        out = []
        for job in data.get("jobs", []):
            location = (job.get("location") or {}).get("name", "")
            depts = ", ".join(d.get("name", "") for d in job.get("departments") or [] if d.get("name"))
            out.append(
                JobPosting(
                    source="greenhouse",
                    external_id=str(job["id"]),
                    company=company.name,
                    title=job.get("title", ""),
                    url=job.get("absolute_url", ""),
                    apply_url=job.get("absolute_url", ""),
                    location=location,
                    remote="remote" in location.lower() if location else None,
                    description=html_to_text(job.get("content", "")),
                    department=depts,
                    posted_at=parse_dt(job.get("first_published") or job.get("updated_at")),
                    board=company.slug,
                )
            )
        return out


class LeverSource(_ATSBase):
    ats = "lever"
    policy = SourcePolicy(
        name="lever",
        terms_url="https://github.com/lever/postings-api",
        min_interval_seconds=2.0,
    )

    def board_url(self, company: Company) -> str:
        host = "api.eu.lever.co" if company.region == "eu" else "api.lever.co"
        return f"https://{host}/v0/postings/{company.slug}"

    def params(self) -> dict:
        return {"mode": "json"}

    def parse(self, company: Company, data) -> list[JobPosting]:
        out = []
        for job in data if isinstance(data, list) else []:
            cats = job.get("categories") or {}
            sections = [job["descriptionPlain"]] if job.get("descriptionPlain") else []
            sections.extend(
                f"{lst.get('text', '')}\n{html_to_text(lst.get('content', ''))}" for lst in job.get("lists") or []
            )
            if job.get("additionalPlain"):
                sections.append(job["additionalPlain"])
            salary = ""
            sr = job.get("salaryRange") or {}
            if sr.get("min") or sr.get("max"):
                salary = f"{sr.get('currency', '')} {sr.get('min', '')}–{sr.get('max', '')} {sr.get('interval', '')}"
            workplace = (job.get("workplaceType") or "").lower()
            out.append(
                JobPosting(
                    source="lever",
                    external_id=str(job["id"]),
                    company=company.name,
                    title=job.get("text", ""),
                    url=job.get("hostedUrl", ""),
                    apply_url=job.get("applyUrl") or job.get("hostedUrl", ""),
                    location=cats.get("location", "") or "",
                    remote=(workplace == "remote") if workplace else None,
                    description="\n\n".join(s.strip() for s in sections if s.strip()),
                    salary=salary.strip(),
                    department=cats.get("team", "") or "",
                    posted_at=parse_dt(job.get("createdAt")),
                    board=company.slug,
                )
            )
        return out


class AshbySource(_ATSBase):
    ats = "ashby"
    policy = SourcePolicy(
        name="ashby",
        terms_url="https://developers.ashbyhq.com/docs/public-job-posting-api",
        min_interval_seconds=2.0,
    )

    def board_url(self, company: Company) -> str:
        return f"https://api.ashbyhq.com/posting-api/job-board/{company.slug}"

    def params(self) -> dict:
        return {"includeCompensation": "true"}

    def parse(self, company: Company, data) -> list[JobPosting]:
        out = []
        for job in data.get("jobs", []):
            if job.get("isListed") is False:
                continue
            comp = job.get("compensation") or {}
            workplace = (job.get("workplaceType") or "").lower()
            remote = job.get("isRemote")
            if remote is None and workplace:
                remote = workplace == "remote"
            out.append(
                JobPosting(
                    source="ashby",
                    external_id=str(job["id"]),
                    company=company.name,
                    title=job.get("title", ""),
                    url=job.get("jobUrl", ""),
                    apply_url=job.get("applyUrl") or job.get("jobUrl", ""),
                    location=job.get("location", "") or "",
                    remote=remote,
                    description=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml", "")),
                    salary=comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary") or "",
                    department=job.get("department", "") or "",
                    posted_at=parse_dt(job.get("publishedAt")),
                    board=company.slug,
                )
            )
        return out
