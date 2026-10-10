from __future__ import annotations

from datetime import timedelta

import respx

from kestrel.compliance.http import PoliteClient
from kestrel.config import update_settings
from kestrel.scanner import upsert_postings
from kestrel.sources.adzuna import AdzunaSource
from kestrel.sources.ats import AshbySource, GreenhouseSource, LeverSource
from kestrel.sources.base import SearchQuery, html_to_text
from kestrel.sources.companies import Company, save_companies
from kestrel.sources.usajobs import USAJobsSource

GH = {
    "jobs": [
        {
            "id": 1,
            "title": "Backend Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
            "location": {"name": "Remote"},
            "content": "&lt;p&gt;Python &amp;amp; Go&lt;/p&gt;",
            "updated_at": "2026-09-01T00:00:00Z",
            "departments": [{"name": "Eng"}],
        }
    ]
}
LEVER = [
    {
        "id": "abc",
        "text": "Data Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/abc",
        "applyUrl": "https://jobs.lever.co/acme/abc/apply",
        "categories": {"location": "NYC", "team": "Data"},
        "descriptionPlain": "Build pipelines",
        "lists": [{"text": "Requirements", "content": "<li>SQL</li>"}],
        "createdAt": 1756684800000,
        "workplaceType": "hybrid",
    }
]
ASHBY = {
    "jobs": [
        {
            "id": "x1",
            "title": "ML Engineer",
            "jobUrl": "https://jobs.ashbyhq.com/acme/x1",
            "applyUrl": "https://jobs.ashbyhq.com/acme/x1/application",
            "location": "London",
            "isRemote": False,
            "descriptionPlain": "Train models",
            "publishedAt": "2026-09-01T00:00:00Z",
            "compensation": {"compensationTierSummary": "£80K – £100K"},
        }
    ]
}


def test_html_to_text_handles_double_encoding():
    assert html_to_text(GH["jobs"][0]["content"]) == "Python & Go"


async def test_ats_sources_and_board_cache():
    save_companies(
        [Company("Acme", "greenhouse", "acme"), Company("Acme", "lever", "acme"), Company("Acme", "ashby", "acme")]
    )
    with respx.mock() as mock:
        gh = mock.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").respond(200, json=GH)
        lv = mock.get("https://api.lever.co/v0/postings/acme").respond(200, json=LEVER)
        ab = mock.get("https://api.ashbyhq.com/posting-api/job-board/acme").respond(200, json=ASHBY)
        async with PoliteClient() as http:
            g = await GreenhouseSource().fetch(http, SearchQuery())
            l_ = await LeverSource().fetch(http, SearchQuery())
            a = await AshbySource().fetch(http, SearchQuery())
            # Second scan within 6 hours: boards are not re-requested.
            assert await GreenhouseSource().fetch(http, SearchQuery()) == []
        assert gh.call_count == lv.call_count == ab.call_count == 1
        assert gh.calls[0].request.url.params["content"] == "true"
    assert g[0].title == "Backend Engineer" and g[0].remote is True and g[0].board == "acme"
    assert l_[0].apply_url.endswith("/apply") and "SQL" in l_[0].description and l_[0].remote is False
    assert a[0].salary == "£80K – £100K"


async def test_adzuna_attribution_and_spacing():
    update_settings({"enable_adzuna": True, "adzuna_app_id": "id", "adzuna_app_key": "key"})
    data = {
        "results": [
            {
                "id": 9,
                "title": "<strong>Python</strong> Dev",
                "redirect_url": "https://www.adzuna.com/land/ad/9",
                "company": {"display_name": "Beta"},
                "location": {"display_name": "Austin"},
                "description": "snippet",
                "salary_min": 100000,
                "salary_max": 120000,
                "salary_is_predicted": "1",
            }
        ]
    }
    with respx.mock() as mock:
        route = mock.get(url__startswith="https://api.adzuna.com/v1/api/jobs/us/search/1").respond(200, json=data)
        async with PoliteClient() as http:
            src = AdzunaSource()
            assert src.enabled()
            jobs = await src.fetch(http, SearchQuery(keywords=["python"]))
            assert await src.fetch(http, SearchQuery(keywords=["python"])) == []  # spaced ≥ 3h
        assert route.call_count == 1
    assert jobs[0].attribution_text == "Jobs by Adzuna"
    assert jobs[0].title == "Python Dev"
    assert "estimate" in jobs[0].salary


async def test_usajobs_headers_and_attribution():
    update_settings({"enable_usajobs": True, "usajobs_api_key": "k", "usajobs_email": "me@example.org"})
    data = {
        "SearchResult": {
            "SearchResultItems": [
                {
                    "MatchedObjectDescriptor": {
                        "PositionID": "P1",
                        "PositionTitle": "IT Specialist",
                        "OrganizationName": "Agency",
                        "PositionURI": "https://www.usajobs.gov/job/1",
                        "ApplyURI": ["https://www.usajobs.gov/job/1/apply"],
                        "PositionLocationDisplay": "DC",
                        "QualificationSummary": "Quals",
                        "UserArea": {"Details": {"JobSummary": "Summary"}},
                    }
                }
            ]
        }
    }
    with respx.mock() as mock:
        route = mock.get(url__startswith="https://data.usajobs.gov/api/search").respond(200, json=data)
        async with PoliteClient() as http:
            jobs = await USAJobsSource().fetch(http, SearchQuery(keywords=["it"]))
        req = route.calls[0].request
        assert req.headers["Authorization-Key"] == "k"
        assert req.headers["User-Agent"] == "me@example.org"
    assert jobs[0].attribution_text == "Source: USAJOBS"
    assert jobs[0].apply_url.startswith("https://www.usajobs.gov")


def test_upsert_dedupes_and_detects_changes():
    from kestrel.sources.base import JobPosting

    p = JobPosting(
        source="greenhouse",
        external_id="1",
        company="Acme",
        title="Eng",
        url="https://x/1",
        location="Remote",
        description="a",
    )
    assert len(upsert_postings([p])) == 1
    assert upsert_postings([p]) == []  # unchanged
    p.description = "b"
    assert len(upsert_postings([p])) == 1  # changed
    dup = JobPosting(
        source="adzuna",
        external_id="9",
        company="ACME",
        title="Eng",
        url="https://y/9",
        location="remote",
        description="z",
    )
    assert upsert_postings([dup]) == []  # same role from another source


def test_parse_dt_is_always_timezone_aware():
    from kestrel.sources.base import parse_dt

    for value in (1756684800000, 1756684800, "2026-09-01T00:00:00Z", "2026-09-01T00:00:00"):
        assert parse_dt(value).tzinfo is not None


def test_upsert_keeps_only_web_links():
    from kestrel.db import session_scope
    from kestrel.models import Job
    from kestrel.sources.base import JobPosting

    bad = JobPosting(source="web", external_id="x", company="A", title="T", url="javascript:alert(1)")
    assert upsert_postings([bad]) == []  # no usable link: not stored
    ok = JobPosting(
        source="web",
        external_id="y",
        company="A",
        title="T",
        url="https://a.example/job",
        apply_url="javascript:alert(1)",
        attribution_url="data:text/html,x",
    )
    (job_id,) = upsert_postings([ok])
    with session_scope() as s:
        job = s.get(Job, job_id)
        assert job
        assert job.apply_url == ""
        assert job.attribution_url == ""


def test_matches_from_older_rules_are_regraded():
    from kestrel.db import session_scope
    from kestrel.models import Classification, Job
    from kestrel.profile.schema import CandidateProfile
    from kestrel.scanner import grading_key, jobs_needing_classification
    from kestrel.sources.base import JobPosting

    profile = CandidateProfile(skills=["Python"])
    (job_id,) = upsert_postings([JobPosting(source="web", external_id="1", company="A", title="T", url="https://a/1")])
    with session_scope() as s:
        job = s.get(Job, job_id)
        assert job
        old = Classification(job_id=job_id, tier="maybe", job_hash=job.content_hash, profile_hash=profile.fingerprint())
        s.add(old)
        s.commit()
    assert [j.id for j in jobs_needing_classification(profile)] == [job_id]  # graded under older rules
    with session_scope() as s:
        job = s.get(Job, job_id)
        assert job
        s.add(Classification(job_id=job_id, tier="good", job_hash=job.content_hash, profile_hash=grading_key(profile)))
        s.commit()
    assert jobs_needing_classification(profile) == []


async def test_one_broken_board_does_not_stop_the_others():
    from kestrel.sources.base import recently_fetched

    save_companies([Company("Broken", "greenhouse", "broken"), Company("Acme", "greenhouse", "acme")])
    source = GreenhouseSource()
    with respx.mock() as mock:
        mock.get("https://boards-api.greenhouse.io/v1/boards/broken/jobs").respond(500)
        mock.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").respond(200, json=GH)
        async with PoliteClient() as http:
            postings = await source.fetch(http, SearchQuery())
    assert [p.board for p in postings] == ["acme"]
    assert len(source.problems) == 1 and source.problems[0].startswith("broken:")
    assert recently_fetched("board:greenhouse:acme", timedelta(hours=1))
    assert not recently_fetched("board:greenhouse:broken", timedelta(hours=1))  # retried next scan


async def test_backoff_keeps_postings_already_read(no_sleep):
    save_companies([Company("Acme", "greenhouse", "acme"), Company("Later", "greenhouse", "later")])
    source = GreenhouseSource()
    with respx.mock() as mock:
        mock.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").respond(200, json=GH)
        mock.get("https://boards-api.greenhouse.io/v1/boards/later/jobs").respond(429, headers={"Retry-After": "7200"})
        async with PoliteClient() as http:
            postings = await source.fetch(http, SearchQuery())
    assert [p.board for p in postings] == ["acme"]
    assert any("later:" in p for p in source.problems)
