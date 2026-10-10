from __future__ import annotations

import json

import httpx
import respx

from kestrel.answers.generate import draft_answers
from kestrel.answers.questions import FormQuestion, fetch_greenhouse_questions, label_kinds, parse_pasted
from kestrel.compliance.http import PoliteClient
from kestrel.llm.provider import ChatClient, LLMConfig, parse_json
from kestrel.models import Job
from kestrel.profile.schema import CandidateProfile

PROFILE = CandidateProfile(name="Sam", skills=["Python"], years_experience=4, target_titles=["Engineer"])
JOB = Job(
    id=1,
    source="greenhouse",
    external_id="7",
    board="acme",
    company="Acme",
    title="Engineer",
    description="Build things",
)


def test_parse_pasted():
    qs = parse_pasted("1. Why us?*\n\n- LinkedIn URL\n")
    assert [q.label for q in qs] == ["Why us?", "LinkedIn URL"]
    assert qs[0].required and not qs[1].required


async def test_greenhouse_questions_marks_demographics():
    data = {
        "questions": [
            {"label": "Resume", "required": True, "fields": [{"name": "resume", "type": "input_file"}]},
            {"label": "Why Acme?", "required": True, "fields": [{"name": "q1", "type": "textarea"}]},
            {
                "label": "Need sponsorship?",
                "required": True,
                "fields": [
                    {
                        "name": "q2",
                        "type": "multi_value_single_select",
                        "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}],
                    }
                ],
            },
        ],
        "compliance": [{"type": "eeoc", "questions": [{"label": "Gender"}]}],
        "location_questions": [{"label": "Location", "required": True}, {"label": "Latitude", "required": True}],
    }
    with respx.mock() as mock:
        route = mock.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs/7").respond(200, json=data)
        async with PoliteClient() as http:
            qs = await fetch_greenhouse_questions(http, JOB)
        assert route.calls[0].request.url.params["questions"] == "true"
    kinds = {q.label: q.kind for q in qs}
    assert kinds["Resume"] == "file_upload"
    assert kinds["Gender"] == "eeo_demographic"
    assert kinds["Latitude"] == "auto_filled" and kinds["Location"] == "short_factual"
    assert next(q for q in qs if q.label == "Need sponsorship?").options == ["Yes", "No"]


async def test_eeo_keywords_skip_without_classifier_call():
    class Boom:
        name = model = "boom"

        async def ask(self, *a):
            raise AssertionError("classifier should not be called")

    qs = await label_kinds(Boom(), [FormQuestion("What is your gender identity?")])
    assert qs[0].kind == "eeo_demographic"


async def test_drafts_skip_eeo_and_keep_placeholders():
    seen = {}

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        seen["questions"] = payload["QUESTIONS"]
        seen["system"] = json.loads(request.content)["messages"][0]["content"]
        content = {
            "answers": [
                {"id": 0, "answer": "I have 4 years of Python experience.", "facts_used": ["years_experience"]},
                {"id": 2, "answer": "[fill in: notice period]"},
            ]
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})

    client = ChatClient(LLMConfig("http://local/v1", "", "m"), transport=httpx.MockTransport(handler))
    questions = [
        FormQuestion("Describe your Python experience", kind="long_form"),
        FormQuestion("Race / ethnicity", kind="eeo_demographic"),
        FormQuestion("Notice period?", kind="short_factual"),
    ]
    results = await draft_answers(PROFILE, JOB, questions, client=client)
    assert [q["question"] for q in seen["questions"]] == ["Describe your Python experience", "Notice period?"]
    assert "Never invent" in seen["system"]
    assert results[1]["skipped"] and results[1]["answer"] == ""
    assert "placeholders" in results[2]["note"]
    assert results[0]["answer"].startswith("I have 4 years")


def test_parse_json_tolerates_wrapping():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('<think>hmm</think>Sure! {"a": 2} hope that helps') == {"a": 2}


AIRBNB_STYLE = (
    "By submitting this application, I agree and understand that the use of generative artificial "
    'intelligence ("AI") tools, for example, ChatGPT, Gemini, and GitHub Copilot ("AI Tools") is '
    "strictly prohibited during the interview process. I further attest that all materials I submit "
    "were completed without the use of AI Tools."
)


def test_ai_policy_keywords():
    from kestrel.classify.ai_policy import keyword_score

    assert keyword_score(AIRBNB_STYLE) == 1.0
    assert keyword_score("Please do not use AI tools to write your answers.") == 1.0
    assert keyword_score("No AI-generated applications will be considered.") == 1.0
    assert keyword_score("You will build AI tools that help millions of users.") == 0.0
    assert keyword_score("Experience with generative AI is a plus.") == 0.0


async def test_attestations_are_never_drafted():
    qs = await label_kinds(
        None,
        [
            FormQuestion("Candidate AI Usage Attestation", description=AIRBNB_STYLE),
            FormQuestion("Before submitting please review", options=["I acknowledge receipt of the Privacy Notice"]),
            FormQuestion("Why do you want this job?"),
        ],
    )
    assert [q.kind for q in qs] == ["attestation", "attestation", ""]
