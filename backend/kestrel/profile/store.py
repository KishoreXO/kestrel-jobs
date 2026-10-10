from __future__ import annotations

from kestrel.db import session_scope
from kestrel.models import Profile, utcnow
from kestrel.profile.schema import CandidateProfile


def load_profile() -> CandidateProfile:
    with session_scope() as session:
        row = session.get(Profile, 1)
        return CandidateProfile.model_validate(row.data) if row and row.data else CandidateProfile()


def save_profile(profile: CandidateProfile, resume_text: str | None = None) -> CandidateProfile:
    with session_scope() as session:
        row = session.get(Profile, 1) or Profile(id=1)
        row.data = profile.model_dump()
        if resume_text is not None:
            row.resume_text = resume_text
        row.updated_at = utcnow()
        session.add(row)
        session.commit()
    return profile
