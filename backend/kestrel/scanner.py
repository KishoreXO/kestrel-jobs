"""One scan: fetch from every enabled source, store new/changed postings, classify them."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlmodel import col, select

from kestrel.classify.base import Classifier, ClassifierError, get_classifier
from kestrel.classify.pipeline import classify_job, matched_skills, prefilter
from kestrel.classify.questions import RULES_VERSION
from kestrel.compliance.denylist import PolicyViolation
from kestrel.compliance.http import PoliteClient, SourceBackoff
from kestrel.config import get_settings
from kestrel.db import session_scope
from kestrel.models import Classification, Job, ScanRun, require_id
from kestrel.profile.schema import CandidateProfile
from kestrel.profile.store import load_profile
from kestrel.sources import ALL_SOURCES
from kestrel.sources.base import JobPosting, SearchQuery, clear_marks, http_url

log = logging.getLogger(__name__)

_scan_lock = asyncio.Lock()
CLASSIFY_CONCURRENCY = 4
GIVE_UP_AFTER = 5
LOGGED_FAILURES = 3


def scan_in_progress() -> bool:
    return _scan_lock.locked()


def query_from_profile(profile: CandidateProfile) -> SearchQuery:
    return SearchQuery(
        keywords=profile.target_titles[:5] or profile.skills[:3],
        locations=profile.preferred_locations[:2] or ([profile.location] if profile.location else []),
        remote_ok=profile.remote_preference in ("remote", "any", "hybrid"),
    )


def upsert_postings(postings: list[JobPosting]) -> list[int]:
    """Stores postings; returns ids of jobs that are new or whose content changed."""
    changed: list[int] = []
    with session_scope() as session:
        for p in postings:
            p.url, p.apply_url, p.attribution_url = http_url(p.url), http_url(p.apply_url), http_url(p.attribution_url)
            if not p.url:
                continue
            existing = session.exec(select(Job).where(Job.source == p.source, Job.external_id == p.external_id)).first()
            digest = p.content_hash()
            if existing and existing.content_hash == digest:
                existing.fetched_at = datetime.now(UTC)
                session.add(existing)
                continue
            # Same role listed by another source: keep the first copy.
            dupe = session.exec(select(Job).where(Job.dedupe_key == p.dedupe_key(), Job.source != p.source)).first()
            if dupe and not existing:
                continue
            job = existing or Job(source=p.source, external_id=p.external_id)
            for field in (
                "company",
                "title",
                "location",
                "remote",
                "url",
                "apply_url",
                "description",
                "salary",
                "department",
                "posted_at",
                "attribution_text",
                "attribution_url",
                "board",
            ):
                setattr(job, field, getattr(p, field))
            job.content_hash = digest
            job.dedupe_key = p.dedupe_key()
            job.fetched_at = datetime.now(UTC)
            session.add(job)
            session.flush()
            changed.append(require_id(job))
        session.commit()
    return changed


def grading_key(profile: CandidateProfile) -> str:
    """Matches are reused only while both the profile and the selection rules are unchanged."""
    return f"{profile.fingerprint()}:r{RULES_VERSION}"


def jobs_needing_classification(profile: CandidateProfile) -> list[Job]:
    fp = grading_key(profile)
    with session_scope() as session:
        jobs = session.exec(select(Job).where(Job.hidden == False)).all()  # noqa: E712
        latest: dict[int, Classification] = {}
        for c in session.exec(select(Classification).order_by(col(Classification.created_at))).all():
            latest[c.job_id] = c
        return [
            j
            for j in jobs
            if not (j.id in latest and latest[j.id].job_hash == j.content_hash and latest[j.id].profile_hash == fp)
        ]


async def classify_one(classifier: Classifier, profile: CandidateProfile, job: Job) -> Classification:
    settings = get_settings()
    skip = prefilter(profile, job.title, job.company, job.description, settings.prefilter)
    if skip:
        result = Classification(job_id=require_id(job), backend="prefilter", tier="rejected", reject_reason=skip)
    else:
        r = await classify_job(classifier, profile, job.model_dump())
        result = Classification(
            job_id=require_id(job),
            backend=classifier.name,
            model=classifier.model,
            tier=r.tier,
            match_score=r.match_score,
            confidence=r.confidence,
            reject_reason=r.reject_reason,
            ai_prohibited=r.ai_prohibited,
            details={**r.details, "matched_skills": matched_skills(profile.skills, f"{job.title}\n{job.description}")},
        )
    result.job_hash = job.content_hash
    result.profile_hash = grading_key(profile)
    with session_scope() as session:
        session.add(result)
        session.commit()
    return result


async def classify_pending(run: ScanRun | None = None) -> int:
    profile = load_profile()
    if not profile.is_usable():
        _log(run, "Profile is empty: add target titles or skills before classifying.")
        return 0
    pending = jobs_needing_classification(profile)
    if not pending:
        return 0
    try:
        classifier = get_classifier()
    except ClassifierError as e:
        _log(run, f"Classifier not ready: {e}")
        return 0
    # Also covers re-grading after a profile or rules change, when no postings are new.
    _log(run, f"Classifying {len(pending)} new or changed {'job' if len(pending) == 1 else 'jobs'}…")

    sem = asyncio.Semaphore(CLASSIFY_CONCURRENCY)
    done = 0
    failed = 0

    async def worker(job: Job):
        nonlocal done, failed
        async with sem:
            # A misconfigured model fails every job: stop early instead of hammering the endpoint.
            if failed >= GIVE_UP_AFTER and done == 0:
                return
            try:
                await classify_one(classifier, profile, job)
                done += 1
            except Exception as e:  # one job's failure must never stop the scan
                failed += 1
                if failed <= LOGGED_FAILURES:
                    _log(run, f"Classification failed for '{job.title}': {e}")

    await asyncio.gather(*(worker(j) for j in pending))
    if failed and done == 0:
        _log(
            run,
            f"Stopped classifying: the classifier failed {failed} times in a row. Check Settings → "
            "Save & test connection, then click Retry classification.",
        )
    elif failed > LOGGED_FAILURES:
        _log(run, f"{failed} jobs couldn't be classified; they stay Pending and are retried next time.")
    return done


def _log(run: ScanRun | None, message: str) -> None:
    log.info(message)
    if run is not None:
        run.log = [*run.log, message]
        _save_progress(run)


def _save_progress(run: ScanRun) -> None:
    """Stores the run so the dashboard can show progress while it's still going."""
    with session_scope() as session:
        session.merge(run)
        session.commit()


async def run_scan(*, fetch: bool = True) -> ScanRun:
    """Fetches from every enabled source (unless `fetch` is False), then classifies what's pending."""
    if _scan_lock.locked():
        raise RuntimeError("A scan is already running")
    async with _scan_lock:
        run = ScanRun()
        with session_scope() as session:
            session.add(run)
            session.commit()
        try:
            if fetch:
                await _fetch_all(run)
            run.classified = await classify_pending(run)
            run.status = "done"
        except Exception as e:
            run.status = "failed"
            _log(run, f"Scan failed: {type(e).__name__}: {e}")
        run.finished_at = datetime.now(UTC)
        _save_progress(run)
        return run


async def _fetch_all(run: ScanRun) -> None:
    query = query_from_profile(load_profile())
    async with PoliteClient() as http:
        for source in ALL_SOURCES:
            if not source.enabled():
                continue
            try:
                postings = await source.fetch(http, query)
                changed = upsert_postings(postings)
            except (SourceBackoff, PolicyViolation) as e:
                _log(run, f"{source.policy.name}: {e}")
                continue
            except Exception as e:  # one broken source must not stop the scan
                clear_marks(source.policy.name)
                _log(run, f"{source.policy.name}: failed ({type(e).__name__}: {str(e)[:300]})")
                continue
            run.fetched += len(postings)
            run.new += len(changed)
            _log(run, f"{source.policy.name}: {len(postings)} postings, {len(changed)} new/changed")
            for problem in getattr(source, "problems", ()):
                _log(run, f"{source.policy.name}: {problem}")
