from __future__ import annotations

import json

import httpx
import pytest
import respx

from kestrel.classify.base import Choice, ChoiceAnswer, Noul, NoulAnswer, Score, ScoreAnswer
from kestrel.classify.jev import ENDPOINT, JevClassifier
from kestrel.classify.llm_backend import LLMClassifier
from kestrel.classify.pipeline import classify_job, decide, prefilter
from kestrel.classify.questions import GATES, SCORES, all_questions
from kestrel.llm.provider import LLMConfig
from kestrel.profile.schema import CandidateProfile

PROFILE = CandidateProfile(
    target_titles=["Backend Engineer", "Python Developer"],
    skills=["Python", "PostgreSQL", "Kubernetes"],
    years_experience=5,
    location="Berlin",
    remote_preference="remote",
    work_authorization=["EU"],
)
JOB = {
    "title": "Senior Backend Engineer",
    "company": "Acme",
    "location": "Remote (EU)",
    "description": "We use Python and PostgreSQL. 4+ years required.",
}


def jev_answers(gates=1.0, score_level=4, ai=0.0):
    answers = {k: {"type": "noul", "noul": gates} for k in GATES}
    answers["ai_prohibited"] = {"type": "noul", "noul": ai}
    for k in SCORES:
        answers[k] = {
            "type": "score",
            "score": score_level,
            "confidence": 0.9,
            "legend": {str(i): "x" for i in range(5)},
            "probabilities": {str(score_level): 0.9},
        }
    answers["seniority"] = {
        "type": "choice",
        "choice": "senior",
        "confidence": 0.8,
        "probabilities": {"senior": 0.8, "mid": 0.2},
    }
    return answers


# ----------------------------------------------------------------- Jev backend
async def test_jev_request_shape_and_parsing():
    with respx.mock() as mock:
        route = mock.post(ENDPOINT).respond(
            200, json={"model": "jev-1.13", "answers": jev_answers(), "usage": {"input_tokens": 10, "output_tokens": 1}}
        )
        jev = JevClassifier(api_key="k")
        result = await classify_job(jev, PROFILE, JOB)
        body = json.loads(route.calls[0].request.content)
        assert route.calls[0].request.headers["Authorization"] == "Bearer k"
        assert body["model"] == "jev-latest"
        assert set(body["questions"]) == set(all_questions())
        assert body["questions"]["seniority"]["type"] == "choice"
        assert body["questions"]["skills_fit"]["criteria"][0] == SCORES["skills_fit"].levels[0]
        assert "candidate" in body["state"] and "email" not in body["state"]
        rule = "Only jobs with at least 85% confidence score must be returned and output."
        assert rule in json.loads(body["state"])["instructions"]
        assert rule in body["questions"]["skills_fit"]["instructions"]
    assert result.tier == "strong"
    assert result.match_score == pytest.approx(1.0)
    assert jev.model == "jev-1.13"


async def test_jev_retries_on_overload(no_sleep):
    with respx.mock() as mock:
        route = mock.post(ENDPOINT).mock(
            side_effect=[
                httpx.Response(529),
                httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 0.7}}}),
            ]
        )
        ans = await JevClassifier(api_key="k").ask("s", {"q": Noul("x")})
        assert route.call_count == 2
        assert ans["q"].value == pytest.approx(0.7)


async def test_jev_needs_key():
    from kestrel.classify.base import ClassifierError

    with pytest.raises(ClassifierError):
        JevClassifier(api_key="")


# ----------------------------------------------------------------- LLM backend
def fake_llm(content: dict):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})

    return httpx.MockTransport(handler)


async def test_llm_classifier_normalises_answers():
    content = {
        "n": {"type": "noul", "value": 0.8},
        "c": {"type": "choice", "probabilities": {"a": 3, "b": 1}},
        "s": {"type": "score", "probabilities": {"0": 0, "1": 0, "2": 1}},
    }
    clf = LLMClassifier(config=LLMConfig("http://local/v1", "", "m"), transport=fake_llm(content))
    ans = await clf.ask(
        "state", {"n": Noul("x"), "c": Choice("y", {"a": "A", "b": "B"}), "s": Score("z", ["l", "m", "h"])}
    )
    assert isinstance(ans["n"], NoulAnswer) and ans["n"].value == 0.8
    assert isinstance(ans["c"], ChoiceAnswer) and ans["c"].choice == "a" and ans["c"].confidence == 0.75
    assert isinstance(ans["s"], ScoreAnswer) and ans["s"].value == 1.0


async def test_llm_classifier_full_pipeline():
    content = {k: {"type": "noul", "value": 1} for k in GATES}
    content["ai_prohibited"] = {"type": "noul", "value": 0}
    for k in SCORES:
        content[k] = {"type": "score", "probabilities": {"3": 1}}
    content["seniority"] = {"type": "choice", "probabilities": {"senior": 1}}
    clf = LLMClassifier(config=LLMConfig("http://local/v1", "", "m"), transport=fake_llm(content))
    result = await classify_job(clf, PROFILE, JOB)
    # Level 3 of 4 is a 75% match: below the 85% bar, so it's filtered out with the score in the reason.
    assert result.tier == "rejected"
    assert result.match_score == pytest.approx(0.75)
    assert "85%" in result.reject_reason


async def test_llm_classifier_is_told_the_85_percent_rule():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"n": {"value": 1}})}}]})

    clf = LLMClassifier(config=LLMConfig("http://local/v1", "", "m"), transport=httpx.MockTransport(handler))
    await clf.ask("state", {"n": Noul("x")})
    prompt = json.dumps(seen[0])
    assert "Only jobs with at least 85% confidence score must be returned and output." in prompt


# ----------------------------------------------------------------- decisions
def _answers(gate=1.0, score=1.0, conf=0.9, ai=0.0):
    a = {k: NoulAnswer(gate) for k in GATES}
    a["ai_prohibited"] = NoulAnswer(ai)
    for k in SCORES:
        a[k] = ScoreAnswer(score, confidence=conf)
    return a


def test_failed_gate_rejects_with_reason():
    a = _answers()
    a["work_auth_ok"] = NoulAnswer(0.1)
    r = decide(a)
    assert r.tier == "rejected"
    assert "authorization" in r.reject_reason


def test_tiers():
    assert decide(_answers(score=0.95)).tier == "strong"
    assert decide(_answers(score=0.92)).tier == "strong"
    assert decide(_answers(score=0.9)).tier == "good"
    assert decide(_answers(score=0.7)).tier == "rejected"
    assert decide(_answers(score=0.3)).tier == "rejected"


def test_85_percent_bar_on_score_and_confidence():
    assert decide(_answers(score=0.85, conf=0.85)).tier == "good"
    below_score = decide(_answers(score=0.849, conf=0.95))
    assert below_score.tier == "rejected"
    assert "85%" in below_score.reject_reason
    assert decide(_answers(score=0.95, conf=0.849)).tier == "rejected"
    assert decide(_answers(score=0.9, conf=0.3)).tier == "rejected"


def test_ai_flag_carried():
    assert decide(_answers(ai=0.9)).ai_prohibited == 0.9


def test_prefilter():
    assert prefilter(PROFILE, "Backend Engineer II", "Acme", "", "strict") is None
    assert prefilter(PROFILE, "Sales Manager", "Acme", "", "strict")
    assert prefilter(PROFILE, "Kubernetes Platform Specialist", "Acme", "", "loose") is None
    assert prefilter(PROFILE, "Sales Manager", "Acme", "", "off") is None
    p = PROFILE.model_copy(update={"excluded_companies": ["Acme"]})
    assert "Excluded" in prefilter(p, "Backend Engineer", "acme", "", "off")


def test_matched_skills_short_names_need_matching_case():
    from kestrel.classify.pipeline import matched_skills

    text = "Grow as you go. We use Go, R and python; c-suite reporting."
    assert matched_skills(["Go", "R", "Python", "C"], text) == ["Go", "R", "Python"]
    assert matched_skills(["Go"], "Grow as you go.") == []
    assert matched_skills(["SQL"], "Strong sql skills") == ["SQL"]


async def test_jev_unreadable_answers_are_classifier_errors():
    from kestrel.classify.base import ClassifierError

    for body in ({"answers": {"q": {"noul": None}}}, ["not", "an", "object"], {"answers": ["q"]}):
        with respx.mock() as mock:
            mock.post(ENDPOINT).respond(200, json=body)
            with pytest.raises(ClassifierError):
                await JevClassifier(api_key="k").ask("s", {"q": Noul("x")})


def test_matched_skills_counts_each_skill_once():
    from kestrel.classify.pipeline import matched_skills

    assert matched_skills(["Python", "python ", "SQL"], "Python and SQL") == ["Python", "SQL"]
