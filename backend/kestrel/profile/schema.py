from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field


class Education(BaseModel):
    degree: str = ""
    field: str = ""
    institution: str = ""
    year: str = ""


class Experience(BaseModel):
    title: str = ""
    company: str = ""
    start: str = ""
    end: str = ""
    highlights: list[str] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    """Everything the matcher and answer assistant may use. Nothing outside this is ever invented."""

    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    headline: str = ""
    summary: str = ""
    years_experience: float | None = None
    target_titles: list[str] = Field(default_factory=list)
    target_seniority: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    links: dict[str, str] = Field(default_factory=dict)

    # Preferences / constraints
    preferred_locations: list[str] = Field(default_factory=list)
    remote_preference: Literal["remote", "hybrid", "onsite", "any"] = "any"
    willing_to_relocate: bool = False
    work_authorization: list[str] = Field(default_factory=list)
    needs_sponsorship: bool = False
    salary_min: int | None = None
    salary_currency: str = "USD"
    notice_period: str = ""
    excluded_companies: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)

    # Free-form facts the user is happy to have used in answers (e.g. "Available from March").
    extra_facts: list[str] = Field(default_factory=list)

    # Learned from thumbs up / down in the dashboard ("Title at Company"); newest last, max 10 each.
    liked_examples: list[str] = Field(default_factory=list)
    disliked_examples: list[str] = Field(default_factory=list)

    def for_matching(self) -> dict:
        """The subset sent to the classifier (no contact details)."""
        data = self.model_dump(exclude={"email", "phone", "links", "name"})
        return {k: v for k, v in data.items() if v not in ("", [], {}, None)}

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.for_matching(), sort_keys=True).encode()).hexdigest()[:16]

    def is_usable(self) -> bool:
        return bool(self.target_titles or self.skills)
