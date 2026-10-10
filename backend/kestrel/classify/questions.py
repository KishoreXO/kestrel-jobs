"""Question sets used by the matching pipeline. Identical for every classifier backend.

Following TypeSafe's guidance, each question is narrow; multi-factor judgements are
decomposed and recombined in code (see pipeline.py).
"""

from __future__ import annotations

from kestrel.classify.base import Choice, Noul, Score

# Sent to every classifier with each job. Kestrel also enforces it in code (pipeline.decide).
MIN_MATCH = 0.85
MIN_CONFIDENCE = 0.85
SELECTION_RULE = (
    "Only jobs with at least 85% confidence score must be returned and output. "
    "Judge strictly: rate a fit highly only when the posting clearly supports it."
)
# Bump whenever the thresholds or the rule text change, so stored matches are re-graded.
RULES_VERSION = 2

# Hard requirements. Missing information in the posting counts in the candidate's favour.
GATES: dict[str, Noul] = {
    "experience_ok": Noul(
        "The candidate meets the job's minimum required years of experience.",
        true="The posting states no minimum, or the candidate's experience meets or exceeds it.",
        false="The posting requires clearly more experience than the candidate has.",
    ),
    "qualifications_ok": Noul(
        "The candidate holds every qualification the posting marks as strictly required "
        "(must-have skills, degrees, licenses, certifications, clearances).",
        true="All hard requirements are met, or the posting lists none.",
        false="At least one strictly required qualification is clearly missing.",
    ),
    "location_ok": Noul(
        "The job's location / remote arrangement is compatible with the candidate's location, "
        "remote preference and relocation willingness.",
        true="Compatible, or the posting does not restrict location.",
        false="The job requires presence somewhere the candidate cannot or will not work.",
    ),
    "work_auth_ok": Noul(
        "The candidate's work authorization satisfies the posting.",
        true="The posting sets no restriction, or the candidate is authorized / sponsorship is offered "
        "when the candidate needs it.",
        false="The posting excludes candidates with the candidate's authorization status "
        "(for example: requires citizenship the candidate lacks, or says no sponsorship while "
        "the candidate needs it).",
    ),
    "legit": Noul(
        "This is a genuine, specific job opening from an employer.",
        true="A real role with concrete responsibilities.",
        false="Spam, an MLM/commission-only pitch, a training-fee scheme, or a generic talent pool.",
    ),
}

# Flag only (never a gate): the employer asks applicants not to use AI tools.
AI_POLICY = {
    "ai_prohibited": Noul(
        "The posting explicitly asks applicants not to use AI tools (such as ChatGPT or other "
        "generative AI) to write their application, answers or materials.",
    )
}

LEVELS_5 = [
    "None",
    "Weak",
    "Partial",
    "Strong",
    "Excellent",
]

SCORES: dict[str, Score] = {
    "title_fit": Score(
        "How closely this role matches the job titles and kind of work the candidate is targeting.",
        [
            "Unrelated kind of work",
            "Tangentially related",
            "Adjacent role",
            "Same role family",
            "Exactly the role the candidate targets",
        ],
    ),
    "skills_fit": Score(
        "How well the candidate's skills cover the skills and tools the job asks for.",
        [
            "Almost none of the requested skills",
            "A few requested skills",
            "About half",
            "Most requested skills",
            "Essentially all requested skills, including the core ones",
        ],
    ),
    "seniority_fit": Score(
        "How well the job's seniority matches the candidate's level and target seniority.",
        [
            "Far off (e.g. intern vs principal)",
            "Two levels apart",
            "One level apart",
            "Slight stretch or slight step down",
            "Exact level match",
        ],
    ),
    "domain_fit": Score(
        "How well the job's industry/domain matches the candidate's background and interests.",
        LEVELS_5,
    ),
    "preference_fit": Score(
        "How well the job satisfies the candidate's stated preferences (salary floor, remote "
        "preference, preferred locations, excluded keywords) and resembles the jobs they liked "
        "rather than the ones they disliked (liked_examples / disliked_examples). Unknown values "
        "are neutral.",
        [
            "Violates a stated preference",
            "Mostly misaligned",
            "Neutral / unknown",
            "Mostly aligned",
            "Fully aligned",
        ],
    ),
}

CHOICES: dict[str, Choice] = {
    "seniority": Choice(
        "The seniority level of this job.",
        {
            "intern": "Internship or apprenticeship",
            "entry": "Entry level / new grad / junior",
            "mid": "Mid level",
            "senior": "Senior",
            "staff": "Staff, principal or lead individual contributor",
            "manager": "People manager",
            "director": "Director, VP or executive",
        },
    ),
}

WEIGHTS = {
    "title_fit": 0.30,
    "skills_fit": 0.30,
    "seniority_fit": 0.20,
    "domain_fit": 0.10,
    "preference_fit": 0.10,
}


def all_questions() -> dict:
    return {**GATES, **AI_POLICY, **SCORES, **CHOICES}
