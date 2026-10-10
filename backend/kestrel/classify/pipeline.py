"""Matching pipeline: local prefilter → typed questions → gates → composite score → tier."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from kestrel.classify.base import Answer, ChoiceAnswer, Classifier, NoulAnswer, ScoreAnswer
from kestrel.classify.questions import (
    GATES,
    MIN_CONFIDENCE,
    MIN_MATCH,
    SCORES,
    SELECTION_RULE,
    WEIGHTS,
    all_questions,
)
from kestrel.profile.schema import CandidateProfile

GATE_THRESHOLD = 0.5
# A job is shown only at MIN_MATCH score and MIN_CONFIDENCE (both 85%); the best of those are "strong".
STRONG = 0.92
MAX_DESCRIPTION_CHARS = 12000

GATE_REASONS = {
    "experience_ok": "Requires more experience than you have",
    "qualifications_ok": "Missing a strictly required qualification",
    "location_ok": "Location / remote arrangement doesn't fit",
    "work_auth_ok": "Work authorization requirement not met",
    "legit": "Doesn't look like a genuine job posting",
}


@dataclass
class MatchResult:
    tier: str
    match_score: float
    confidence: float
    reject_reason: str = ""
    ai_prohibited: float = 0.0
    details: dict = field(default_factory=dict)


_WORD = re.compile(r"[a-z0-9+#]+")
_STOP = {
    "and",
    "or",
    "of",
    "the",
    "a",
    "an",
    "to",
    "in",
    "for",
    "i",
    "ii",
    "iii",
    "iv",
    "sr",
    "jr",
    "senior",
    "junior",
    "lead",
    "staff",
    "principal",
    "head",
    "remote",
    "hybrid",
}


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if t not in _STOP and len(t) > 1}


def prefilter(profile: CandidateProfile, title: str, company: str, description: str, mode: str) -> str | None:
    """Cheap local filter run before any model call. Returns a reason when the job is skipped."""
    low_title, low_desc = title.lower(), description.lower()
    for c in profile.excluded_companies:
        if c and c.lower() == company.lower():
            return f"Excluded company: {company}"
    for kw in profile.excluded_keywords:
        if kw and (kw.lower() in low_title or (mode == "strict" and kw.lower() in low_desc)):
            return f"Excluded keyword: {kw}"
    if mode == "off" or not profile.target_titles:
        return None
    title_tokens = _tokens(title)
    target_tokens = set().union(*(_tokens(t) for t in profile.target_titles))
    if title_tokens & target_tokens:
        return None
    if mode == "loose":
        skill_tokens = set().union(*(_tokens(s) for s in profile.skills)) if profile.skills else set()
        if title_tokens & skill_tokens:
            return None
    return "Title doesn't resemble your target roles"


# Short skill names that are also everyday words ("Go", "R", "C") only count with matching case.
CASE_SENSITIVE_MAX_LEN = 2


def matched_skills(skills: list[str], text: str) -> list[str]:
    """Profile skills that appear in the posting as whole words. No model needed."""
    low = text.lower()
    found: list[str] = []
    seen: set[str] = set()  # "Python" listed twice counts once
    for skill in skills:
        s = skill.strip()
        if not s or s.lower() in seen:
            continue
        haystack, needle = (text, s) if len(s) <= CASE_SENSITIVE_MAX_LEN else (low, s.lower())
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])", haystack):
            found.append(s)
            seen.add(s.lower())
    return found


def build_state(profile: CandidateProfile, job: dict) -> str:
    posting = {
        k: v
        for k, v in {
            "title": job.get("title"),
            "company": job.get("company"),
            "location": job.get("location"),
            "remote": job.get("remote"),
            "salary": job.get("salary"),
            "department": job.get("department"),
            "description": (job.get("description") or "")[:MAX_DESCRIPTION_CHARS],
        }.items()
        if v not in (None, "")
    }
    return json.dumps(
        {"instructions": SELECTION_RULE, "job_posting": posting, "candidate": profile.for_matching()},
        ensure_ascii=False,
        indent=1,
    )


def _serialise(ans: Answer) -> dict:
    if isinstance(ans, NoulAnswer):
        return {"type": "noul", "value": round(ans.value, 4)}
    if isinstance(ans, ChoiceAnswer):
        return {
            "type": "choice",
            "choice": ans.choice,
            "confidence": round(ans.confidence, 4),
            "probabilities": {k: round(v, 4) for k, v in ans.probabilities.items()},
        }
    return {
        "type": "score",
        "value": round(ans.value, 4),
        "confidence": round(ans.confidence, 4),
        "probabilities": {k: round(v, 4) for k, v in ans.probabilities.items()},
    }


def decide(answers: Mapping[str, Answer]) -> MatchResult:
    details = {k: _serialise(v) for k, v in answers.items()}
    ai = answers.get("ai_prohibited")
    ai_value = ai.value if isinstance(ai, NoulAnswer) else 0.0

    failed = [k for k in GATES if isinstance(a := answers.get(k), NoulAnswer) and a.value < GATE_THRESHOLD]
    if failed:
        return MatchResult(
            tier="rejected",
            match_score=0.0,
            confidence=1.0,
            reject_reason="; ".join(GATE_REASONS[k] for k in failed),
            ai_prohibited=ai_value,
            details=details,
        )

    score = 0.0
    confidences = []
    for key, weight in WEIGHTS.items():
        ans = answers.get(key)
        if isinstance(ans, ScoreAnswer):
            score += weight * ans.value
            confidences.append(ans.confidence)
    # Rounded before comparing, so a weighted sum like 0.84999999 counts as the 0.85 it represents.
    score = round(score, 4)
    confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0

    reason = ""
    if score >= MIN_MATCH and confidence >= MIN_CONFIDENCE:
        tier = "strong" if score >= STRONG else "good"
    else:
        tier = "rejected"
        reason = f"Below the 85% bar (match {score:.0%}, confidence {confidence:.0%})"
    return MatchResult(
        tier=tier,
        match_score=score,
        confidence=confidence,
        reject_reason=reason,
        ai_prohibited=ai_value,
        details=details,
    )


async def classify_job(classifier: Classifier, profile: CandidateProfile, job: dict) -> MatchResult:
    answers = await classifier.ask(build_state(profile, job), all_questions())
    return decide(answers)


__all__ = ["SCORES", "MatchResult", "build_state", "classify_job", "decide", "prefilter"]
