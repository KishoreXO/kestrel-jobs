from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, col, func, select

from kestrel.answers.generate import draft_answers
from kestrel.answers.questions import FormQuestion, fetch_greenhouse_questions, label_kinds, parse_pasted
from kestrel.classify.ai_policy import THRESHOLD as AI_POLICY_THRESHOLD
from kestrel.classify.ai_policy import ai_policy_score, keyword_score
from kestrel.classify.base import ClassifierError, Noul, get_classifier
from kestrel.classify.pipeline import matched_skills
from kestrel.compliance.denylist import DENYLIST, PolicyViolation
from kestrel.compliance.http import PoliteClient, SourceBackoff
from kestrel.config import get_settings, public_settings, update_settings
from kestrel.db import get_session
from kestrel.llm.provider import ChatClient, LLMConfig, LLMError, LLMNotConfigured, list_models
from kestrel.models import AnswerDraft, AuditLog, Classification, Job, ScanRun, require_id, utcnow
from kestrel.profile.importer import extract_text, structure_resume
from kestrel.profile.schema import CandidateProfile
from kestrel.profile.store import load_profile, save_profile
from kestrel.scanner import run_scan, scan_in_progress, upsert_postings
from kestrel.scheduler import apply_schedule
from kestrel.sources import ALL_SOURCES
from kestrel.sources.companies import Company, load_companies, load_starter, save_companies
from kestrel.sources.feeds import Feed, load_feeds, save_feeds

router = APIRouter(prefix="/api")
UTF8_BOM = chr(0xFEFF)


# ---------------------------------------------------------------- health / settings
@router.get("/health")
def health():
    return {"ok": True}


def _settings_out() -> dict:
    return {
        "settings": public_settings(get_settings()),
        "sources": [
            {
                "name": src.policy.name,
                "terms_url": src.policy.terms_url,
                "attribution": src.policy.attribution_text,
                "enabled": src.enabled(),
            }
            for src in ALL_SOURCES
        ],
        "denylist": sorted(DENYLIST),
    }


@router.get("/settings")
def get_settings_route():
    return _settings_out()


@router.put("/settings")
def put_settings(changes: dict):
    """Returns the same shape as GET, so the dashboard also sees which sources are now active."""
    try:
        update_settings(changes)
    except ValidationError as e:  # one plain line per bad field, not the validator's full report
        raise HTTPException(422, "; ".join(_field_error(err) for err in e.errors())) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    apply_schedule()
    return _settings_out()


def _field_error(err) -> str:
    field = ".".join(str(p) for p in err.get("loc", ())) or "value"
    return f"{field}: {str(err.get('msg', 'invalid')).removeprefix('Value error, ')}"


class TestRequest(BaseModel):
    target: str  # "classifier" | "llm"


def _friendly_error(e: Exception, base_url: str = "") -> str:
    if isinstance(e, httpx.ConnectError):
        return (
            f"Couldn't connect to {base_url or 'the endpoint'}. Is the server running "
            "(for example Ollama or LM Studio), and is the address right?"
        )
    if isinstance(e, httpx.TimeoutException):
        return "The model took too long to answer. Try a smaller model or check the server."
    if isinstance(e, httpx.InvalidURL | httpx.UnsupportedProtocol):
        return f"{base_url or 'The address'} isn't a valid URL. It should look like http://localhost:11434/v1"
    text = str(e)
    if "401" in text or "403" in text:
        return f"The API key was rejected. Check it was copied in full. {text[:200]}"
    if "404" in text:
        hint = "" if base_url.rstrip("/").endswith("/v1") else " Most servers need the address to end in /v1."
        return f"Endpoint or model not found. Check the base URL and model name.{hint} {text[:200]}"
    return text[:300] or type(e).__name__


def _llm_config(target: str) -> LLMConfig:
    if target == "llm":
        return LLMConfig.for_drafting()
    if target == "classifier":
        return LLMConfig.for_classification()
    raise HTTPException(422, "target must be 'classifier' or 'llm'")


@router.get("/settings/models")
async def available_models(target: str = "llm"):
    """Model ids the configured endpoint offers, so users can pick one instead of typing it."""
    config = _llm_config(target)
    try:
        return {"ok": True, "models": await list_models(config), "message": ""}
    except Exception as e:  # report every failure in plain words
        return {"ok": False, "models": [], "message": _friendly_error(e, config.base_url)}


@router.post("/settings/test")
async def test_connection(req: TestRequest):
    """Sends one tiny request to the model the user configured, so setup problems show up early."""
    s = get_settings()
    if req.target == "llm":
        config = LLMConfig.for_drafting()
        try:
            reply = await ChatClient(config, timeout=60).chat(
                [{"role": "user", "content": "Reply with the single word OK."}], max_tokens=20
            )
        except Exception as e:  # report every failure in plain words
            return {"ok": False, "message": _friendly_error(e, config.base_url)}
        return {"ok": True, "message": f"Connected to {config.model}. It replied: {reply.strip()[:60]!r}"}
    if req.target == "classifier":
        base = LLMConfig.for_classification().base_url if s.classifier == "llm" else "the Jev API"
        try:
            answers = await get_classifier().ask("The sky is blue.", {"t": Noul("The statement mentions a colour.")})
        except Exception as e:  # report every failure in plain words
            return {"ok": False, "message": _friendly_error(e, base)}
        value = getattr(answers.get("t"), "value", 0.0)
        name = "Jev" if s.classifier == "jev" else LLMConfig.for_classification().model
        return {"ok": True, "message": f"{name} answered a test question (score {value:.2f})."}
    raise HTTPException(422, "target must be 'classifier' or 'llm'")


def _has_entries(load: Callable[[], list]) -> bool:
    try:
        return bool(load())
    except ValueError:  # an unreadable list isn't a usable source; the Companies page explains why
        return False


@router.get("/setup-status")
def setup_status():
    s = get_settings()
    if s.classifier == "jev":
        classifier_ready = bool(s.typesafe_api_key)
    else:
        config = LLMConfig.for_classification()
        classifier_ready = bool(config.base_url and config.model)
    sources_ready = (
        _has_entries(load_companies)
        or _has_entries(load_feeds)
        or (s.enable_usajobs and bool(s.usajobs_api_key))
        or (s.enable_adzuna and bool(s.adzuna_app_key))
    )
    steps = {
        "contact_email": bool(s.contact_email),
        "classifier": classifier_ready,
        "drafting_model": bool(s.llm_base_url and s.llm_model),
        "profile": load_profile().is_usable(),
        "sources": sources_ready,
    }
    return {"steps": steps, "complete": all(steps.values())}


# ---------------------------------------------------------------- profile
@router.get("/profile")
def get_profile():
    return load_profile().model_dump()


@router.put("/profile")
def put_profile(profile: CandidateProfile):
    return save_profile(profile).model_dump()


MAX_RESUME_BYTES = 10 * 1024 * 1024


@router.post("/profile/import")
async def import_resume(file: UploadFile = File(...)):
    too_large = HTTPException(413, "Resume file too large (max 10 MB)")
    if file.size is not None and file.size > MAX_RESUME_BYTES:
        raise too_large
    data = await file.read(MAX_RESUME_BYTES + 1)
    if len(data) > MAX_RESUME_BYTES:
        raise too_large
    try:
        text = extract_text(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    try:
        extracted = await structure_resume(text)
        # Only what the resume actually filled in replaces your profile: preferences, exclusions and
        # thumbs up/down history aren't in a resume and must survive an import.
        profile = CandidateProfile.model_validate(
            {**load_profile().model_dump(), **extracted.model_dump(exclude_defaults=True)}
        )
        structured = True
        message = "Extracted your profile. Review every field before saving."
    except Exception as e:  # the text is still saved; explain why structuring didn't happen
        profile = load_profile()
        structured = False
        reason = str(e) if isinstance(e, LLMNotConfigured) else _friendly_error(e, LLMConfig.for_drafting().base_url)
        message = f"Resume text imported, but automatic structuring didn't work: {reason} Fill the fields manually."
    save_profile(load_profile(), resume_text=text)
    return {"profile": profile.model_dump(), "structured": structured, "message": message, "resume_text": text[:20000]}


# ---------------------------------------------------------------- companies
class CompanyIn(BaseModel):
    name: str
    ats: str
    slug: str
    region: str = ""


@router.get("/companies")
def get_companies():
    try:
        return [c.__dict__ for c in load_companies()]
    except ValueError as e:  # a hand-edited companies.yaml: say which entry is wrong
        raise HTTPException(422, f"companies.yaml: {e}") from e


@router.get("/companies/starter")
def get_starter_companies():
    return {group: [c.__dict__ for c in items] for group, items in load_starter().items()}


@router.put("/companies")
def put_companies(companies: list[CompanyIn]):
    try:
        save_companies([Company(**c.model_dump()) for c in companies])
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return [c.__dict__ for c in load_companies()]


# ---------------------------------------------------------------- feeds
class FeedIn(BaseModel):
    name: str
    url: str
    allowed: bool = False


@router.get("/feeds")
def get_feeds():
    try:
        return [f.__dict__ for f in load_feeds()]
    except ValueError as e:  # a hand-edited feeds.yaml: say which entry is wrong
        raise HTTPException(422, f"feeds.yaml: {e}") from e


@router.put("/feeds")
def put_feeds(feeds: list[FeedIn]):
    try:
        save_feeds([Feed(**f.model_dump()) for f in feeds])
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return [f.__dict__ for f in load_feeds()]


# ---------------------------------------------------------------- scanning
async def _run_scan_quietly(*, fetch: bool = True) -> None:
    """Background entry point: a second click that slipped past the 409 check is simply dropped."""
    if not scan_in_progress():
        await run_scan(fetch=fetch)


@router.post("/scan")
async def start_scan(background: BackgroundTasks):
    if scan_in_progress():
        raise HTTPException(409, "A scan is already running")
    background.add_task(_run_scan_quietly)
    return {"started": True}


@router.post("/classify")
async def classify_now(background: BackgroundTasks):
    """Classifies pending jobs without fetching (e.g. after fixing the model settings)."""
    if scan_in_progress():
        raise HTTPException(409, "A scan is already running")
    background.add_task(_run_scan_quietly, fetch=False)
    return {"started": True}


@router.get("/scan/status")
def scan_status(session: Session = Depends(get_session)):
    last = session.exec(select(ScanRun).order_by(col(ScanRun.id).desc())).first()
    return {"running": scan_in_progress(), "last": last.model_dump() if last else None}


# ---------------------------------------------------------------- jobs
def _latest_classifications(session: Session) -> dict[int, Classification]:
    latest: dict[int, Classification] = {}
    for c in session.exec(select(Classification).order_by(col(Classification.created_at))).all():
        latest[c.job_id] = c
    return latest


def _job_out(job: Job, c: Classification | None, full: bool = False) -> dict:
    data = job.model_dump(exclude={"description"} if not full else set())
    if not full:
        data["snippet"] = job.description[:280]
    data["classification"] = c.model_dump() if c else None
    return data


STATUSES = ("new", "saved", "applied", "interviewing", "offer", "rejected", "archived")


@router.get("/jobs")
def list_jobs(
    tier: str = "", q: str = "", status: str = "", include_hidden: bool = False, session: Session = Depends(get_session)
):
    """`status` empty means every job you haven't archived; otherwise only that tracker status."""
    latest = _latest_classifications(session)
    jobs = [j for j in session.exec(select(Job)).all() if include_hidden or not j.hidden]
    status_counts: dict[str, int] = {}
    for job in jobs:
        status_counts[job.status] = status_counts.get(job.status, 0) + 1
    jobs = [j for j in jobs if j.status == status] if status else [j for j in jobs if j.status != "archived"]
    ql = q.lower().strip()
    if ql:
        jobs = [j for j in jobs if ql in f"{j.title} {j.company} {j.location}".lower()]
    counts: dict[str, int] = {}
    out = []
    for job in jobs:
        c = latest.get(require_id(job))
        job_tier = c.tier if c else "pending"
        counts[job_tier] = counts.get(job_tier, 0) + 1
        if tier and tier != job_tier:
            continue
        out.append(_job_out(job, c))
    out.sort(key=lambda j: -((j["classification"] or {}).get("match_score") or 0))
    return {"jobs": out, "counts": counts, "status_counts": status_counts}


FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(*cells: str) -> list[str]:
    """Posting text comes from third parties: never let a cell run as a spreadsheet formula."""
    return [f"'{c}" if c.startswith(FORMULA_PREFIXES) else c for c in cells]


@router.get("/jobs/export.csv")
def export_jobs(session: Session = Depends(get_session)):
    """Your tracked jobs (anything you saved, applied to or moved further) as a spreadsheet."""
    latest = _latest_classifications(session)
    jobs = [j for j in session.exec(select(Job)).all() if j.status not in ("new", "archived")]
    jobs.sort(key=lambda j: (STATUSES.index(j.status) if j.status in STATUSES else 99, j.company.lower()))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Status",
            "Title",
            "Company",
            "Location",
            "Match tier",
            "Match score",
            "Status changed",
            "First seen",
            "Notes",
            "Posting URL",
        ]
    )
    for j in jobs:
        c = latest.get(require_id(j))
        writer.writerow(
            _csv_safe(
                j.status,
                j.title,
                j.company,
                j.location,
                c.tier if c else "pending",
                f"{c.match_score:.2f}" if c else "",
                j.status_changed_at.date().isoformat() if j.status_changed_at else "",
                j.first_seen.date().isoformat() if j.first_seen else "",
                j.notes,
                j.url,
            )
        )
    return Response(
        content=UTF8_BOM + buf.getvalue(),  # so Excel reads UTF-8 correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="kestrel-applications.csv"'},
    )


def _get_job(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session)):
    job = _get_job(session, job_id)
    c = session.exec(
        select(Classification).where(Classification.job_id == job_id).order_by(col(Classification.created_at).desc())
    ).first()
    drafts = session.exec(select(AnswerDraft).where(AnswerDraft.job_id == job_id).order_by(col(AnswerDraft.id))).all()
    out = _job_out(job, c, full=True)
    out["drafts"] = [d.model_dump() for d in drafts]
    out["questions_available"] = job.source == "greenhouse" and bool(job.board)
    return out


@router.post("/jobs/{job_id}/hide")
def hide_job(job_id: int, hidden: bool = True, session: Session = Depends(get_session)):
    job = _get_job(session, job_id)
    job.hidden = hidden
    session.add(job)
    session.commit()
    return {"hidden": hidden}


class JobUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None


@router.patch("/jobs/{job_id}")
def update_job(job_id: int, body: JobUpdate, session: Session = Depends(get_session)):
    """Local application tracker. Changing a status never touches the employer's site."""
    job = _get_job(session, job_id)
    if body.status is not None:
        if body.status not in STATUSES:
            raise HTTPException(422, f"status must be one of {', '.join(STATUSES)}")
        if body.status != job.status:
            job.status = body.status
            job.status_changed_at = utcnow()
    if body.notes is not None:
        job.notes = body.notes[:20000]
    session.add(job)
    session.commit()
    return job.model_dump(exclude={"description"})


class FeedbackIn(BaseModel):
    value: int  # 1 = more like this, -1 = less like this, 0 = clear


FEEDBACK_MEMORY = 10


@router.post("/jobs/{job_id}/feedback")
def job_feedback(job_id: int, body: FeedbackIn, session: Session = Depends(get_session)):
    """Thumbs up/down, remembered as examples in your profile so future matching learns from them."""
    if body.value not in (-1, 0, 1):
        raise HTTPException(422, "value must be -1, 0 or 1")
    job = _get_job(session, job_id)
    example = f"{job.title} at {job.company}".strip()
    profile = load_profile()
    liked = [e for e in profile.liked_examples if e != example]
    disliked = [e for e in profile.disliked_examples if e != example]
    if body.value == 1:
        liked.append(example)
    elif body.value == -1:
        disliked.append(example)
        if job.status in ("new", "saved"):
            job.status = "archived"
            job.status_changed_at = utcnow()
    profile.liked_examples = liked[-FEEDBACK_MEMORY:]
    profile.disliked_examples = disliked[-FEEDBACK_MEMORY:]
    save_profile(profile)
    job.feedback = body.value
    session.add(job)
    session.commit()
    return {"feedback": job.feedback, "status": job.status}


SKILLS_PROMPT = (
    "List the skills, tools and qualifications this job posting says are required or strongly "
    "preferred. Copy each one exactly as written in the posting; never add anything that is not "
    'in it. Return JSON {"skills": ["..."]} with at most 15 items.\n\n'
)


@router.get("/jobs/{job_id}/skills")
async def job_skills(job_id: int, refresh: bool = False, session: Session = Depends(get_session)):
    """Skills you have that the posting mentions, plus must-haves you may be missing (model-assisted)."""
    job = _get_job(session, job_id)
    profile = load_profile()
    text = f"{job.title}\n{job.description}"
    matched = matched_skills(profile.skills, text)
    c = session.exec(
        select(Classification).where(Classification.job_id == job_id).order_by(col(Classification.created_at).desc())
    ).first()
    cached = (c.details or {}).get("missing_skills") if c else None
    if cached is not None and not refresh:
        return {"matched": matched, "missing": cached, "message": None}
    try:
        data = await ChatClient(LLMConfig.for_drafting()).chat_json(
            [{"role": "user", "content": SKILLS_PROMPT + text[:12000]}], temperature=0.0, max_tokens=500
        )
    except (LLMNotConfigured, LLMError, httpx.HTTPError) as e:
        return {
            "matched": matched,
            "missing": None,
            "message": f"Set a drafting model in Settings to see missing skills ({type(e).__name__}).",
        }
    wanted = data.get("skills", []) if isinstance(data, dict) else []
    have = {m.lower() for m in profile.skills}
    # Grounded: keep only items that appear in the posting as whole words and that you don't already list.
    missing = []
    for item in wanted:
        w = item.strip() if isinstance(item, str) else ""
        if w and matched_skills([w], text) and w.lower() not in have and not matched_skills(profile.skills, w):
            missing.append(w)
            have.add(w.lower())  # the model may list the same skill twice
    missing = missing[:15]
    if c:
        c.details = {**(c.details or {}), "missing_skills": missing}
        session.add(c)
        session.commit()
    return {"matched": matched, "missing": missing, "message": None}


@router.post("/jobs/{job_id}/reclassify")
async def reclassify(job_id: int, session: Session = Depends(get_session)):
    from kestrel.scanner import classify_one

    job = _get_job(session, job_id)
    profile = load_profile()
    if not profile.is_usable():
        raise HTTPException(422, "Add target titles or skills to your profile first")
    try:
        c = await classify_one(get_classifier(), profile, job)
    except ClassifierError as e:
        raise HTTPException(502, str(e)) from e
    return c.model_dump()


# ---------------------------------------------------------------- answer assistant
class AnswerRequest(BaseModel):
    pasted: str = ""
    questions: list[FormQuestion] | None = None


def _classifier_or_none():
    try:
        return get_classifier()
    except ClassifierError:
        return None


AI_REFUSAL = (
    "This employer asks applicants not to use AI tools for their application. "
    "Kestrel won't draft answers for this job. Please write them yourself."
)


async def _ai_policy(job: Job, c: Classification | None, questions: list[FormQuestion]) -> float:
    """Checks the posting and the form (labels, help text, options) for a no-AI policy."""
    form_text = "\n".join(f"{q.label}\n{q.description}\n{' '.join(q.options)}" for q in questions)
    known = c.ai_prohibited if c and c.backend not in ("", "prefilter") else 0.0
    if known >= AI_POLICY_THRESHOLD:
        return known
    classifier = _classifier_or_none()
    description_score = (
        known if c and c.backend not in ("", "prefilter") else await ai_policy_score(classifier, job.description)
    )
    form_score = await ai_policy_score(classifier, form_text) if form_text.strip() else 0.0
    return max(description_score, form_score, keyword_score(job.description))


CLOSED_POSTING = "This posting is no longer on Greenhouse (it may have closed). Paste the questions instead."


@router.get("/jobs/{job_id}/questions")
async def get_questions(job_id: int, session: Session = Depends(get_session)):
    job = _get_job(session, job_id)
    if job.source != "greenhouse" or not job.board:
        return {
            "questions": [],
            "source": "paste",
            "message": "This source doesn't publish its form questions. Paste them from the application page.",
        }
    try:
        async with PoliteClient() as http:
            questions = await fetch_greenhouse_questions(http, job)
    except (SourceBackoff, PolicyViolation) as e:
        raise HTTPException(503, str(e)) from e
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"questions": [], "source": "paste", "message": CLOSED_POSTING}
        raise HTTPException(502, f"Greenhouse returned {e.response.status_code}. Paste the questions instead.") from e
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(
            502, f"Couldn't read the form from Greenhouse ({type(e).__name__}). Paste the questions instead."
        ) from e
    c = session.exec(
        select(Classification).where(Classification.job_id == job_id).order_by(col(Classification.created_at).desc())
    ).first()
    ai = await _ai_policy(job, c, questions)
    return {
        "questions": [q.to_dict() for q in questions],
        "source": "greenhouse",
        "ai_prohibited": ai >= AI_POLICY_THRESHOLD,
        "message": AI_REFUSAL if ai >= AI_POLICY_THRESHOLD else None,
    }


@router.post("/jobs/{job_id}/answers")
async def make_answers(job_id: int, req: AnswerRequest, session: Session = Depends(get_session)):
    job = _get_job(session, job_id)
    c = session.exec(
        select(Classification).where(Classification.job_id == job_id).order_by(col(Classification.created_at).desc())
    ).first()
    questions = req.questions if req.questions else parse_pasted(req.pasted)
    if not questions:
        raise HTTPException(422, "No questions provided")
    if len(questions) > 60:
        raise HTTPException(422, "Too many questions (max 60)")
    if await _ai_policy(job, c, questions) >= AI_POLICY_THRESHOLD:
        raise HTTPException(409, AI_REFUSAL)
    profile = load_profile()
    questions = await label_kinds(_classifier_or_none(), questions)
    try:
        results = await draft_answers(profile, job, questions)
    except LLMNotConfigured as e:
        raise HTTPException(422, str(e)) from e
    except (LLMError, httpx.HTTPError) as e:  # e.g. the model server isn't running
        raise HTTPException(502, _friendly_error(e, LLMConfig.for_drafting().base_url)) from e

    for old in session.exec(select(AnswerDraft).where(AnswerDraft.job_id == job_id)).all():
        session.delete(old)
    for r in results:
        session.add(
            AnswerDraft(
                job_id=job_id, question=r["question"], kind=r["kind"] or "", answer=r["answer"], note=r.get("note", "")
            )
        )
    session.commit()
    drafts = session.exec(select(AnswerDraft).where(AnswerDraft.job_id == job_id).order_by(col(AnswerDraft.id))).all()
    return {"drafts": [d.model_dump() for d in drafts]}


class DraftUpdate(BaseModel):
    answer: str


@router.put("/drafts/{draft_id}")
def update_draft(draft_id: int, body: DraftUpdate, session: Session = Depends(get_session)):
    draft = session.get(AnswerDraft, draft_id)
    if not draft:
        raise HTTPException(404, "Draft not found")
    draft.answer = body.answer
    draft.updated_at = utcnow()
    session.add(draft)
    session.commit()
    return draft.model_dump()


# ---------------------------------------------------------------- browsing
_browse_state = {"running": False, "log": [], "finished_at": None}


@router.post("/browse")
async def start_browse(background: BackgroundTasks):
    if not get_settings().browse_enabled:
        raise HTTPException(403, "Browsing is disabled in Settings")
    if _browse_state["running"]:
        raise HTTPException(409, "Browsing already running")

    async def task():
        from kestrel.browse.agent import browse_sites

        try:
            postings = await browse_sites(_browse_state["log"].append)
            changed = upsert_postings(postings)
            _browse_state["log"].append(f"Collected {len(postings)} postings ({len(changed)} new)")
            if changed and not scan_in_progress():
                run = await run_scan(fetch=False)
                _browse_state["log"].append(f"Classified {run.classified} jobs")
        except Exception as e:  # surface every failure to the user
            _browse_state["log"].append(f"Browsing stopped: {type(e).__name__}: {e}")
        finally:
            _browse_state.update(running=False, finished_at=datetime.now(UTC).isoformat())

    _browse_state.update(running=True, log=[], finished_at=None)  # before the task, so a double click can't start two
    background.add_task(task)
    return {"started": True}


@router.get("/browse/status")
def browse_status():
    return _browse_state


# ---------------------------------------------------------------- audit
@router.get("/audit")
def audit_log(limit: int = 200, session: Session = Depends(get_session)):
    limit = max(1, min(limit, 1000))  # SQLite reads a negative LIMIT as "no limit"
    rows = session.exec(select(AuditLog).order_by(col(AuditLog.id).desc()).limit(limit)).all()
    total = session.exec(select(func.count()).select_from(AuditLog)).one()
    blocked = session.exec(select(func.count()).select_from(AuditLog).where(AuditLog.blocked_reason != "")).one()
    return {"entries": [r.model_dump() for r in rows], "total": total, "blocked": blocked}
