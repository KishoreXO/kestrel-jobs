from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from kestrel.models import Classification, Job


@pytest.fixture
def client():
    from kestrel.main import create_app

    with TestClient(create_app(), base_url="http://localhost") as c:
        yield c


def _add_job(ai=0.0):
    from kestrel.db import session_scope

    with session_scope() as s:
        job = Job(
            source="lever",
            external_id="1",
            company="Acme",
            title="Engineer",
            url="https://jobs.lever.co/acme/1",
            description="Build things",
        )
        s.add(job)
        s.commit()
        s.add(Classification(job_id=job.id, backend="jev", tier="strong", match_score=0.9, ai_prohibited=ai))
        s.commit()
        return job.id


def test_settings_masks_secrets(client):
    r = client.put("/api/settings", json={"typesafe_api_key": "secret-key", "classifier": "jev"})
    assert r.status_code == 200
    body = client.get("/api/settings").json()
    assert body["settings"]["typesafe_api_key"] == "set"
    assert "secret-key" not in json.dumps(body)
    assert "linkedin.com" in body["denylist"]


def test_settings_reject_unknown_and_short_interval(client):
    assert client.put("/api/settings", json={"data_dir": "/tmp"}).status_code == 422
    client.put("/api/settings", json={"scan_interval_hours": 1})
    assert client.get("/api/settings").json()["settings"]["scan_interval_hours"] >= 6


def test_profile_roundtrip(client):
    r = client.put("/api/profile", json={"name": "Sam", "skills": ["Python"], "target_titles": ["Engineer"]})
    assert r.status_code == 200
    assert client.get("/api/profile").json()["skills"] == ["Python"]


def test_jobs_listing(client):
    _add_job()
    data = client.get("/api/jobs").json()
    assert data["counts"]["strong"] == 1
    assert data["jobs"][0]["url"] == "https://jobs.lever.co/acme/1"


def test_answers_blocked_when_employer_prohibits_ai(client):
    job_id = _add_job(ai=0.95)
    r = client.post(f"/api/jobs/{job_id}/answers", json={"pasted": "Why us?"})
    assert r.status_code == 409
    assert "not to use AI" in r.json()["detail"]


def test_answers_flow(client, monkeypatch):
    job_id = _add_job()
    client.put("/api/settings", json={"llm_base_url": "http://local/v1", "llm_model": "m", "classifier": "llm"})

    def handler(request):
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        if "strict, careful classifier" in system:
            asked = json.loads(body["messages"][1]["content"].split("QUESTIONS:\n", 1)[1])
            content = {}
            if "kind" in asked:
                content["kind"] = {"type": "choice", "probabilities": {"long_form": 1}}
            if "ai" in asked:
                content["ai"] = {"type": "noul", "value": 0.0}
        else:
            content = {"answers": [{"id": 0, "answer": "Because of the mission."}]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def patched(*a, **kw):
        kw["transport"] = transport
        return real(*a, **kw)

    monkeypatch.setattr("kestrel.llm.provider.httpx.AsyncClient", patched)
    r = client.post(f"/api/jobs/{job_id}/answers", json={"pasted": "Why us?"})
    assert r.status_code == 200, r.text
    drafts = r.json()["drafts"]
    assert drafts[0]["answer"] == "Because of the mission."
    assert drafts[0]["kind"] == "long_form"
    d = client.put(f"/api/drafts/{drafts[0]['id']}", json={"answer": "Edited"}).json()
    assert d["answer"] == "Edited"


def test_browse_disabled_by_default(client):
    assert client.post("/api/browse").status_code == 403


def test_audit_endpoint(client):
    assert client.get("/api/audit").json()["total"] == 0


def test_answers_refused_when_form_has_ai_attestation(client):
    from kestrel.db import session_scope

    with session_scope() as s:
        job = Job(
            source="greenhouse",
            external_id="5",
            board="acme",
            company="Acme",
            title="Engineer",
            url="https://boards.greenhouse.io/acme/jobs/5",
            description="Build things",
        )
        s.add(job)
        s.commit()
        job_id = job.id
    q = {
        "label": "Candidate AI Usage Attestation",
        "required": True,
        "kind": "",
        "options": [],
        "description": "I attest my answers were completed without the use of AI tools.",
    }
    r = client.post(f"/api/jobs/{job_id}/answers", json={"questions": [q]})
    assert r.status_code == 409


def test_status_tracker_and_filter(client):
    job_id = _add_job()
    assert client.patch(f"/api/jobs/{job_id}", json={"status": "applied", "notes": "Sent 9 Oct"}).status_code == 200
    data = client.get("/api/jobs?status=applied").json()
    assert [j["id"] for j in data["jobs"]] == [job_id]
    assert data["jobs"][0]["notes"] == "Sent 9 Oct"
    assert data["status_counts"] == {"applied": 1}
    assert client.get("/api/jobs?status=saved").json()["jobs"] == []
    assert client.patch(f"/api/jobs/{job_id}", json={"status": "bogus"}).status_code == 422


def test_feedback_updates_profile_and_archives(client):
    from kestrel.profile.store import load_profile

    client.put("/api/profile", json={"skills": ["Python"], "target_titles": ["Engineer"]})
    before = load_profile().fingerprint()
    job_id = _add_job()
    r = client.post(f"/api/jobs/{job_id}/feedback", json={"value": -1}).json()
    assert r["status"] == "archived"
    profile = load_profile()
    assert profile.disliked_examples == ["Engineer at Acme"]
    assert profile.fingerprint() != before  # matches are re-scored with the new preference
    assert client.get("/api/jobs").json()["jobs"] == []  # archived jobs leave the main list
    client.post(f"/api/jobs/{job_id}/feedback", json={"value": 1})
    profile = load_profile()
    assert profile.liked_examples == ["Engineer at Acme"] and profile.disliked_examples == []


def test_setup_status(client):
    steps = client.get("/api/setup-status").json()["steps"]
    assert steps["contact_email"] is True
    assert steps["classifier"] is False and steps["profile"] is False and steps["sources"] is False
    client.put("/api/companies", json=[{"name": "Acme", "ats": "lever", "slug": "acme"}])
    client.put("/api/profile", json={"skills": ["Python"]})
    steps = client.get("/api/setup-status").json()["steps"]
    assert steps["sources"] and steps["profile"]


def test_starter_pack(client):
    data = client.get("/api/companies/starter").json()
    assert "software" in data
    assert all(c["ats"] in ("greenhouse", "lever", "ashby") for group in data.values() for c in group)


def test_connection_test_reports_plain_errors(client, monkeypatch):
    client.put("/api/settings", json={"llm_base_url": "http://local/v1", "llm_model": "m"})

    def handler(request):
        return httpx.Response(401, json={"error": "bad key"})

    _mock_llm(monkeypatch, handler)
    r = client.post("/api/settings/test", json={"target": "llm"}).json()
    assert r["ok"] is False and "key was rejected" in r["message"]

    _mock_llm(monkeypatch, lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]}))
    r = client.post("/api/settings/test", json={"target": "llm"}).json()
    assert r["ok"] is True


def test_missing_skills_are_grounded(client, monkeypatch):
    from kestrel.db import session_scope

    client.put("/api/profile", json={"skills": ["Python"], "target_titles": ["Engineer"]})
    client.put("/api/settings", json={"llm_base_url": "http://local/v1", "llm_model": "m"})
    with session_scope() as s:
        job = Job(
            source="lever",
            external_id="9",
            company="Acme",
            title="Engineer",
            url="https://x",
            description="Must know Python, Kubernetes and Terraform. Good government work.",
        )
        s.add(job)
        s.commit()
        s.add(Classification(job_id=job.id, backend="jev", tier="good", match_score=0.7))
        s.commit()
        job_id = job.id
    content = json.dumps({"skills": ["Python", "Kubernetes", "Terraform", "Rust", "Go"]})
    _mock_llm(monkeypatch, lambda req: httpx.Response(200, json={"choices": [{"message": {"content": content}}]}))
    data = client.get(f"/api/jobs/{job_id}/skills").json()
    assert data["matched"] == ["Python"]
    # "Rust" isn't in the posting and "Go" only appears inside other words, so both are dropped.
    assert data["missing"] == ["Kubernetes", "Terraform"]


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _mock_llm(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real = _REAL_ASYNC_CLIENT

    def patched(*a, **kw):
        kw["transport"] = transport
        return real(*a, **kw)

    monkeypatch.setattr("kestrel.llm.provider.httpx.AsyncClient", patched)


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3

    from kestrel.config import get_settings
    from kestrel.db import get_engine

    db = get_settings().db_path
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE job (id INTEGER PRIMARY KEY, source VARCHAR NOT NULL, external_id VARCHAR NOT NULL, "
        "title VARCHAR)"
    )
    con.execute("INSERT INTO job (source, external_id, title) VALUES ('lever', '1', 'Old')")
    con.commit()
    con.close()
    get_engine.cache_clear()
    get_engine()
    cols = {row[1] for row in sqlite3.connect(db).execute("PRAGMA table_info(job)")}
    assert {"status", "notes", "feedback", "first_seen"} <= cols
    assert sqlite3.connect(db).execute("SELECT status, feedback FROM job").fetchone() == ("new", 0)


def test_models_endpoint(client, monkeypatch):
    client.put("/api/settings", json={"llm_base_url": "http://local/v1/", "llm_model": ""})
    _mock_llm(monkeypatch, lambda req: httpx.Response(200, json={"data": [{"id": "llama"}, {"id": "gemma"}]}))
    assert client.get("/api/settings/models?target=llm").json()["models"] == ["gemma", "llama"]
    _mock_llm(monkeypatch, lambda req: httpx.Response(404, text="not found"))
    r = client.get("/api/settings/models?target=classifier").json()
    assert r["ok"] is False and "not found" in r["message"]
    assert client.get("/api/settings/models?target=bogus").status_code == 422


def test_connection_error_is_plain(client):
    client.put("/api/settings", json={"llm_base_url": "http://127.0.0.1:9/v1", "llm_model": "m"})
    r = client.post("/api/settings/test", json={"target": "llm"}).json()
    assert r["ok"] is False and "Couldn't connect" in r["message"]


def test_setup_status_llm_classifier_needs_endpoint(client):
    client.put("/api/settings", json={"classifier": "llm", "llm_base_url": "", "llm_model": "m"})
    assert client.get("/api/setup-status").json()["steps"]["classifier"] is False
    client.put("/api/settings", json={"llm_base_url": "http://localhost:1234/v1"})
    assert client.get("/api/setup-status").json()["steps"]["classifier"] is True


def test_csv_export(client):
    job_id = _add_job()
    assert client.get("/api/jobs/export.csv").text.strip().count("\n") == 0  # header only: nothing tracked
    client.patch(f"/api/jobs/{job_id}", json={"status": "applied", "notes": "Called, café recruiter"})
    r = client.get("/api/jobs/export.csv")
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.content.decode("utf-8-sig").strip().splitlines()
    assert lines[0].startswith("Status,Title,Company")
    assert lines[1].startswith("applied,Engineer,Acme") and "café" in lines[1]


def test_classifier_gives_up_on_broken_model(client, monkeypatch):
    """A misconfigured model must not be called once per job, and the log must say what to do."""
    import asyncio

    from kestrel.db import session_scope
    from kestrel.scanner import run_scan

    client.put("/api/profile", json={"skills": ["Python"], "target_titles": ["Engineer"]})
    client.put("/api/settings", json={"classifier": "llm", "llm_base_url": "http://local/v1", "llm_model": "m"})
    with session_scope() as s:
        for i in range(30):
            s.add(
                Job(
                    source="lever",
                    external_id=str(i),
                    company="Acme",
                    title="Python Engineer",
                    url=f"https://jobs.lever.co/acme/{i}",
                    description="Python",
                )
            )
        s.commit()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404, text="model not found")

    _mock_llm(monkeypatch, handler)
    run = asyncio.run(run_scan(fetch=False))
    assert run.classified == 0
    assert len(calls) < 30
    assert any("Stopped classifying" in line for line in run.log)
    assert sum("Classification failed" in line for line in run.log) <= 3


def test_resume_import_errors_are_readable(client):
    bad_pdf = client.post("/api/profile/import", files={"file": ("cv.pdf", b"%PDF-1.4 broken", "application/pdf")})
    assert bad_pdf.status_code == 422 and "Couldn't read this file" in bad_pdf.json()["detail"]
    empty = client.post("/api/profile/import", files={"file": ("cv.txt", b"   ", "text/plain")})
    assert empty.status_code == 422 and "No text found" in empty.json()["detail"]
    client.put("/api/settings", json={"llm_base_url": "http://127.0.0.1:9/v1", "llm_model": "m"})
    r = client.post("/api/profile/import", files={"file": ("cv.txt", b"Sam, Python engineer", "text/plain")})
    assert r.status_code == 200 and r.json()["structured"] is False
    assert "Couldn't connect" in r.json()["message"]


def test_csv_export_neutralises_formulas(client):
    from kestrel.db import session_scope

    job_id = _add_job()
    with session_scope() as s:
        job = s.get(Job, job_id)
        assert job
        job.title = '=HYPERLINK("http://evil.example","x")'
        job.status = "saved"
        s.add(job)
        s.commit()
    text = client.get("/api/jobs/export.csv").text
    assert "'=HYPERLINK" in text
    assert ",=HYPERLINK" not in text


def test_rejects_foreign_host_header(client):
    # DNS rebinding: a web page pointing its own domain at 127.0.0.1.
    r = client.get("/api/settings", headers={"host": "attacker.example"})
    assert r.status_code == 400


def test_rejects_cross_site_writes(client):
    r = client.post("/api/scan", headers={"origin": "https://attacker.example"})
    assert r.status_code == 403
    r = client.put("/api/settings", json={"contact_email": "x@y.z"}, headers={"origin": "http://localhost:3000"})
    assert r.status_code == 200


def test_api_docs_hidden_by_default(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_rejects_unsafe_company_slugs(client):
    bad = [{"name": "X", "ats": "greenhouse", "slug": s} for s in ("../admin", "a?b=1", "a#b")]
    for company in bad:
        assert client.put("/api/companies", json=[company]).status_code == 422


def test_double_scan_click_is_dropped_quietly(client, monkeypatch):
    from kestrel.api import routes

    calls = []

    async def fake_run_scan(*, fetch=True):
        calls.append(fetch)

    monkeypatch.setattr(routes, "run_scan", fake_run_scan)
    monkeypatch.setattr(routes, "scan_in_progress", lambda: len(calls) > 0)
    assert client.post("/api/scan").status_code == 200
    # The 409 check passed earlier but a scan started in between: the background task drops it.
    import asyncio

    asyncio.run(routes._run_scan_quietly())
    assert calls == [True]


def test_audit_limit_is_clamped(client):
    from kestrel.compliance import audit

    for i in range(3):
        audit.record(f"https://example.org/{i}", purpose="test", status=200)
    assert len(client.get("/api/audit?limit=-1").json()["entries"]) == 1
    assert len(client.get("/api/audit?limit=2").json()["entries"]) == 2


def test_hand_edited_lists_explain_the_problem(client):
    from kestrel.config import get_settings

    get_settings().feeds_file.write_text("- name: Broken\n  url: ftp://x\n  allowed: true\n", encoding="utf-8")
    get_settings().companies_file.write_text("just: [a, mapping", encoding="utf-8")
    r = client.get("/api/feeds")
    assert r.status_code == 422 and "feeds.yaml" in r.json()["detail"]
    r = client.get("/api/companies")
    assert r.status_code == 422 and "valid YAML" in r.json()["detail"]
    assert client.get("/api/setup-status").json()["steps"]["sources"] is False
    assert client.get("/api/settings").status_code == 200


def test_closed_greenhouse_posting_falls_back_to_paste(client):
    import respx

    from kestrel.db import session_scope

    with session_scope() as s:
        job = Job(
            source="greenhouse",
            external_id="42",
            company="Acme",
            title="Engineer",
            url="https://boards.greenhouse.io/acme/jobs/42",
            board="acme",
        )
        s.add(job)
        s.commit()
        job_id = job.id
    url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/42"
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url).respond(404)
        r = client.get(f"/api/jobs/{job_id}/questions")
        assert r.status_code == 200
        assert r.json()["source"] == "paste" and "no longer on Greenhouse" in r.json()["message"]
        route.respond(500)
        from kestrel.compliance.ratelimit import limiter

        limiter.reset()
        r = client.get(f"/api/jobs/{job_id}/questions")
        assert r.status_code == 502 and "Paste the questions" in r.json()["detail"]


def test_adzuna_country_must_be_a_two_letter_code(client):
    r = client.put("/api/settings", json={"adzuna_country": "usa"})
    assert r.status_code == 422 and "two-letter" in r.json()["detail"]
    r = client.put("/api/settings", json={"adzuna_country": " GB "})
    assert r.status_code == 200 and r.json()["settings"]["adzuna_country"] == "gb"


def test_bad_saved_setting_does_not_stop_startup():
    from kestrel.config import get_settings

    get_settings().settings_file.write_text(json.dumps({"adzuna_country": "us/../x", "auto_scan": True}))
    get_settings.cache_clear()
    s = get_settings()
    assert s.adzuna_country == "us" and s.auto_scan is True


def test_answers_explain_an_unreachable_model(client, monkeypatch):
    from kestrel.api import routes

    job_id = _add_job()

    async def refused(*a, **kw):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(routes, "draft_answers", refused)
    r = client.post(f"/api/jobs/{job_id}/answers", json={"pasted": "Why us?"})
    assert r.status_code == 502 and "Couldn't connect" in r.json()["detail"]


def test_resume_import_keeps_what_a_resume_cannot_contain(client, monkeypatch):
    from kestrel.api import routes
    from kestrel.profile.schema import CandidateProfile
    from kestrel.profile.store import load_profile, save_profile

    save_profile(CandidateProfile(skills=["Python"], excluded_keywords=["intern"], liked_examples=["Engineer at Acme"]))

    async def structured(text):
        return CandidateProfile(name="Sam", skills=["Go", "SQL"])

    monkeypatch.setattr(routes, "structure_resume", structured)
    r = client.post("/api/profile/import", files={"file": ("cv.txt", b"Sam. Go, SQL.", "text/plain")})
    profile = r.json()["profile"]
    assert r.json()["structured"] is True
    assert profile["name"] == "Sam" and profile["skills"] == ["Go", "SQL"]
    assert profile["excluded_keywords"] == ["intern"] and profile["liked_examples"] == ["Engineer at Acme"]
    assert load_profile().liked_examples == ["Engineer at Acme"]


async def test_resume_structuring_keeps_valid_fields(monkeypatch):
    from kestrel.config import update_settings
    from kestrel.llm.provider import ChatClient
    from kestrel.profile.importer import structure_resume

    update_settings({"llm_base_url": "http://local/v1", "llm_model": "m"})

    async def reply(self, messages, **kw):
        return {"name": "Sam", "skills": ["Go"], "years_experience": "6+ years"}

    monkeypatch.setattr(ChatClient, "chat_json", reply)
    profile = await structure_resume("resume text")
    assert profile.name == "Sam" and profile.skills == ["Go"] and profile.years_experience is None


def test_saving_settings_reports_active_sources(client):
    r = client.put("/api/settings", json={"enable_lever": False})
    sources = {s["name"]: s["enabled"] for s in r.json()["sources"]}
    assert sources["lever"] is False and sources["greenhouse"] is True


def test_invalid_setting_is_one_plain_line(client):
    detail = client.put("/api/settings", json={"browse_max_pages_per_domain": 0}).json()["detail"]
    assert detail.startswith("browse_max_pages_per_domain:") and "\n" not in detail
