"""USAJOBS Search API (https://developer.usajobs.gov).

Terms we follow: each user registers their own key; data is shown unaltered with USAJOBS
credited; applicants are always sent to USAJOBS to apply; raw feeds are never redistributed.
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


class USAJobsSource:
    policy = SourcePolicy(
        name="usajobs",
        terms_url="https://developer.usajobs.gov/guides/terms-of-use",
        min_interval_seconds=3.0,
        attribution_text="Source: USAJOBS",
        attribution_url="https://www.usajobs.gov",
        max_requests_per_scan=10,
    )

    def enabled(self) -> bool:
        s = get_settings()
        return s.enable_usajobs and bool(s.usajobs_api_key and s.usajobs_email)

    async def fetch(self, http: PoliteClient, query: SearchQuery) -> list[JobPosting]:
        # Searches are repeated at most once an hour, even if the user clicks "Scan now" repeatedly.
        if recently_fetched("search:usajobs", timedelta(hours=1)):
            return []
        mark_fetched("search:usajobs")
        s = get_settings()
        headers = {
            "Host": "data.usajobs.gov",
            # USAJOBS requires the registered email as the User-Agent.
            "User-Agent": s.usajobs_email,
            "Authorization-Key": s.usajobs_api_key,
        }
        out: dict[str, JobPosting] = {}
        locations = query.locations or [""]
        budget = self.policy.max_requests_per_scan
        for keyword in query.keywords[:5] or [""]:
            for location in locations[:2]:
                if budget <= 0:
                    break
                budget -= 1
                params = {"Keyword": keyword, "ResultsPerPage": 100}
                if location:
                    params["LocationName"] = location
                if query.remote_ok:
                    params["RemoteIndicator"] = "True" if not location else None
                params = {k: v for k, v in params.items() if v not in (None, "")}
                resp = await http.get(
                    "https://data.usajobs.gov/api/search",
                    purpose="usajobs search",
                    min_interval=self.policy.min_interval_seconds,
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                for p in self.parse(resp.json()):
                    out[p.external_id] = p
        return list(out.values())

    def parse(self, data) -> list[JobPosting]:
        items = ((data or {}).get("SearchResult") or {}).get("SearchResultItems") or []
        out = []
        for item in items:
            d = item.get("MatchedObjectDescriptor") or {}
            details = ((d.get("UserArea") or {}).get("Details")) or {}
            pay = (d.get("PositionRemuneration") or [{}])[0]
            salary = ""
            if pay.get("MinimumRange"):
                salary = f"${pay.get('MinimumRange')}–${pay.get('MaximumRange')} {pay.get('Description', '')}".strip()
            description = "\n\n".join(
                x
                for x in [
                    details.get("JobSummary", ""),
                    d.get("QualificationSummary", ""),
                    "\n".join(details.get("MajorDuties") or []),
                    details.get("Requirements", ""),
                    details.get("Education", ""),
                ]
                if x
            )
            apply = (d.get("ApplyURI") or [d.get("PositionURI", "")])[0]
            out.append(
                JobPosting(
                    source="usajobs",
                    external_id=str(d.get("PositionID") or item.get("MatchedObjectId")),
                    company=d.get("OrganizationName", ""),
                    title=d.get("PositionTitle", ""),
                    url=d.get("PositionURI", ""),
                    apply_url=apply,
                    location=d.get("PositionLocationDisplay", ""),
                    remote=str(details.get("RemoteIndicator", "")).lower() == "true" or None,
                    description=html_to_text(description),
                    salary=salary,
                    department=d.get("DepartmentName", ""),
                    posted_at=parse_dt(d.get("PublicationStartDate")),
                    attribution_text=self.policy.attribution_text,
                    attribution_url=self.policy.attribution_url,
                )
            )
        return out
