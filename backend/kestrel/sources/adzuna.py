"""Adzuna Jobs API (https://developer.adzuna.com).

Terms we follow: each user registers their own app id/key; every listing shown carries a
"Jobs by Adzuna" link back to Adzuna; we stay far below the default quota
(25/min, 250/day) by capping calls per scan and scans to every >= 6 hours.
"""

from __future__ import annotations

from datetime import timedelta

from kestrel.compliance.http import PoliteClient
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


class AdzunaSource:
    policy = SourcePolicy(
        name="adzuna",
        terms_url="https://developer.adzuna.com/docs/terms_of_service",
        min_interval_seconds=3.0,  # ≤ 20/min, under the 25/min default
        attribution_text="Jobs by Adzuna",
        attribution_url="https://www.adzuna.com",
        max_requests_per_scan=10,  # with the 3h search spacing below: ≤ 80/day
    )

    def enabled(self) -> bool:
        s = get_settings()
        return s.enable_adzuna and bool(s.adzuna_app_id and s.adzuna_app_key)

    async def fetch(self, http: PoliteClient, query: SearchQuery) -> list[JobPosting]:
        # Searches run at most every 3 hours, even if the user clicks "Scan now" repeatedly.
        if recently_fetched("search:adzuna", timedelta(hours=3)):
            return []
        mark_fetched("search:adzuna")
        s = get_settings()
        country = (s.adzuna_country or "us").lower()
        out: dict[str, JobPosting] = {}
        budget = self.policy.max_requests_per_scan
        for keyword in query.keywords[:5] or [""]:
            for location in (query.locations or [""])[:2]:
                if budget <= 0:
                    break
                budget -= 1
                params = {
                    "app_id": s.adzuna_app_id,
                    "app_key": s.adzuna_app_key,
                    "results_per_page": 50,
                    "what": keyword,
                    "max_days_old": 30,
                    "content-type": "application/json",
                }
                if location:
                    params["where"] = location
                resp = await http.get(
                    f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
                    purpose="adzuna search",
                    min_interval=self.policy.min_interval_seconds,
                    params={k: v for k, v in params.items() if v != ""},
                )
                resp.raise_for_status()
                for p in self.parse(resp.json()):
                    out[p.external_id] = p
        return list(out.values())

    def parse(self, data) -> list[JobPosting]:
        out = []
        for job in (data or {}).get("results", []):
            salary = ""
            if job.get("salary_min"):
                salary = f"{int(job['salary_min'])}–{int(job.get('salary_max') or job['salary_min'])}"
                if str(job.get("salary_is_predicted")) == "1":
                    salary += " (Adzuna estimate)"
            out.append(
                JobPosting(
                    source="adzuna",
                    external_id=str(job["id"]),
                    company=(job.get("company") or {}).get("display_name", ""),
                    title=html_to_text(job.get("title", "")),
                    url=job.get("redirect_url", ""),
                    apply_url=job.get("redirect_url", ""),
                    location=(job.get("location") or {}).get("display_name", ""),
                    description=html_to_text(job.get("description", "")),
                    salary=salary,
                    department=(job.get("category") or {}).get("label", ""),
                    posted_at=parse_dt(job.get("created")),
                    attribution_text=self.policy.attribution_text,
                    attribution_url=self.policy.attribution_url,
                )
            )
        return out
