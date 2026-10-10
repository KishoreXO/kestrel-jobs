"""Collect the application-form questions for a job, without ever filling or submitting the form."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from kestrel.classify.base import Choice, ChoiceAnswer, Classifier, ClassifierError
from kestrel.compliance.http import PoliteClient
from kestrel.models import Job
from kestrel.sources.base import html_to_text

KINDS = {
    "short_factual": "A short factual answer (name, years, URL, location, start date, etc.)",
    "yes_no": "A yes/no or single-select question about eligibility or logistics",
    "long_form": "An open-ended or essay question (motivation, experience, cover letter)",
    "salary": "Expected or desired compensation",
    "eeo_demographic": "Voluntary self-identification: gender, race, ethnicity, veteran, disability, "
    "sexual orientation, age or similar demographic/EEO questions",
    "file_upload": "A request to upload a file (resume, CV, cover letter, portfolio)",
    "attestation": "An acknowledgement, consent, agreement or attestation the applicant must personally "
    "read and confirm (privacy notice, terms, truthfulness, AI-use or policy attestation)",
}

EEO_KEYWORDS = (
    "gender",
    "race",
    "ethnic",
    "hispanic",
    "latino",
    "veteran",
    "disability",
    "disabled",
    "sexual orientation",
    "transgender",
    "pronoun",
    "date of birth",
    "age range",
    "religion",
)

# Never drafted by the assistant.
SKIPPED_KINDS = {
    "eeo_demographic": "Voluntary and personal. Answer it yourself (or decline to self-identify).",
    "file_upload": "Upload your own document on the employer's site.",
    "attestation": "A personal acknowledgement or attestation. Read it on the employer's form and confirm it yourself.",
    "auto_filled": "Filled in automatically by the employer's form (for example, map coordinates from your location).",
}

# Hidden fields some forms fill in themselves.
AUTO_FILLED_LABELS = {"latitude", "longitude"}

ATTESTATION_KEYWORDS = (
    "acknowledge",
    "attest",
    "i agree",
    "i consent",
    "i certify",
    "i confirm",
    "privacy notice",
    "privacy policy",
    "terms and conditions",
    "attestation",
)


@dataclass
class FormQuestion:
    label: str
    required: bool = False
    kind: str = ""
    options: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _field_kind(field_type: str, label: str = "") -> str:
    if field_type == "input_file":
        return "file_upload"
    if field_type == "input_hidden" or label.strip().lower() in AUTO_FILLED_LABELS:
        return "auto_filled"
    return ""


async def fetch_greenhouse_questions(http: PoliteClient, job: Job) -> list[FormQuestion]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{job.board}/jobs/{job.external_id}"
    resp = await http.get(
        url, purpose="greenhouse application questions", min_interval=2.0, params={"questions": "true"}
    )
    resp.raise_for_status()
    data = resp.json()
    out: list[FormQuestion] = []
    for q in data.get("questions") or []:
        fields = q.get("fields") or [{}]
        first = fields[0]
        options = [v.get("label", "") for v in first.get("values") or [] if v.get("label")]
        out.append(
            FormQuestion(
                label=q.get("label", "").strip(),
                required=bool(q.get("required")),
                kind=_field_kind(first.get("type", ""), q.get("label", "")),
                options=options,
                description=html_to_text(q.get("description") or ""),
            )
        )
    for q in data.get("location_questions") or []:
        label = q.get("label", "Location")
        out.append(
            FormQuestion(label=label, required=bool(q.get("required")), kind=_field_kind("", label) or "short_factual")
        )
    # Compliance / demographic sections are voluntary self-identification: listed, never drafted.
    out.extend(
        FormQuestion(label=q.get("label", "Self-identification"), kind="eeo_demographic")
        for section in data.get("compliance") or []
        for q in section.get("questions") or []
    )
    demo = data.get("demographic_questions") or {}
    out.extend(
        FormQuestion(label=q.get("name") or q.get("label", "Demographic question"), kind="eeo_demographic")
        for q in demo.get("questions") or []
    )
    return [q for q in out if q.label]


def parse_pasted(text: str) -> list[FormQuestion]:
    """One question per non-empty line (a trailing '*' marks it required)."""
    out = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-•0123456789.) ").strip()
        if not line:
            continue
        required = line.endswith("*")
        out.append(FormQuestion(label=line.rstrip("* ").strip(), required=required))
    return out


async def label_kinds(classifier: Classifier | None, questions: list[FormQuestion]) -> list[FormQuestion]:
    """Uses the configured classifier to label each unlabelled question's kind."""
    for q in questions:
        # Belt and braces: demographic questions are never drafted, even if the model mislabels them.
        if not q.kind and any(w in q.label.lower() for w in EEO_KEYWORDS):
            q.kind = "eeo_demographic"
        text = f"{q.label} {q.description} {' '.join(q.options)}".lower()
        if not q.kind and any(w in text for w in ATTESTATION_KEYWORDS):
            q.kind = "attestation"
    todo = [q for q in questions if not q.kind]
    if not todo or classifier is None:
        return questions
    question = Choice("What kind of application-form question is this?", KINDS)
    for q in todo:
        state = q.label + (f"\nOptions: {', '.join(q.options)}" if q.options else "")
        try:
            ans = (await classifier.ask(state, {"kind": question}))["kind"]
        except ClassifierError:
            return questions  # unlabelled questions are drafted as general questions
        q.kind = ans.choice if isinstance(ans, ChoiceAnswer) and ans.choice in KINDS else "long_form"
    return questions
