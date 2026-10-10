"""Classifier backed by any model you choose, hosted API or local.

Use this if you prefer not to use Jev. Jev remains the recommended backend: a general chat
model is slower, costlier per decision, and its self-reported probabilities are less
calibrated. Answers are normalised into the same typed primitives either way.
"""

from __future__ import annotations

import json

import httpx

from kestrel.classify.base import (
    Answer,
    Choice,
    ChoiceAnswer,
    ClassifierError,
    Noul,
    NoulAnswer,
    Question,
    ScoreAnswer,
)
from kestrel.classify.questions import SELECTION_RULE
from kestrel.llm.provider import ChatClient, LLMConfig, LLMError, LLMNotConfigured

SYSTEM = (
    """You are a strict, careful classifier. You read a STATE and answer typed QUESTIONS about it.
Judge only from the STATE. Return ONLY a JSON object with one key per question id:
- noul questions:   {"type": "noul", "value": <0..1, how true the statement is>}
- choice questions: {"type": "choice", "probabilities": {<option>: <0..1>, ...}}  (cover every option, sum to 1)
- score questions:  {"type": "score", "probabilities": {"<level index>": <0..1>, ...}}  (levels are 0-based, sum to 1)
No explanations, no extra keys.
Selection rule: """
    + SELECTION_RULE
)


def _describe(q: Question) -> dict:
    if isinstance(q, Noul):
        d: dict = {"type": "noul", "statement": q.instructions}
        if q.true:
            d["true_means"] = q.true
        if q.false:
            d["false_means"] = q.false
        return d
    if isinstance(q, Choice):
        return {"type": "choice", "question": q.instructions, "options": q.criteria}
    return {"type": "score", "question": q.instructions, "levels": {str(i): text for i, text in enumerate(q.levels)}}


def _normalise(probs: dict, keys: list[str]) -> dict[str, float]:
    cleaned = {}
    for k in keys:
        try:
            cleaned[k] = max(0.0, float(probs.get(k, 0.0)))
        except (TypeError, ValueError):
            cleaned[k] = 0.0
    total = sum(cleaned.values())
    if total <= 0:
        return {k: 1.0 / len(keys) for k in keys}
    return {k: v / total for k, v in cleaned.items()}


def _parse(q: Question, raw) -> Answer:
    raw = raw if isinstance(raw, dict) else {}
    if isinstance(q, Noul):
        try:
            value = float(raw.get("value", raw.get("noul", 0.5)))
        except (TypeError, ValueError):
            value = 0.5
        return NoulAnswer(value=min(1.0, max(0.0, value)))
    if isinstance(q, Choice):
        keys = list(q.criteria)
        probs = raw.get("probabilities")
        if not isinstance(probs, dict):
            picked = raw.get("choice")
            probs = {k: (1.0 if k == picked else 0.0) for k in keys}
        probs = _normalise(probs, keys)
        best = max(probs, key=lambda k: probs[k])
        return ChoiceAnswer(choice=best, probabilities=probs, confidence=probs[best])
    keys = [str(i) for i in range(len(q.levels))]
    probs = raw.get("probabilities")
    if not isinstance(probs, dict):
        level = str(raw.get("level", raw.get("score", 0)))
        probs = {k: (1.0 if k == level else 0.0) for k in keys}
    probs = _normalise(probs, keys)
    expected = sum(int(k) * p for k, p in probs.items())
    top = max(len(keys) - 1, 1)
    return ScoreAnswer(value=expected / top, probabilities=probs, confidence=max(probs.values()))


class LLMClassifier:
    name = "llm"

    def __init__(self, *, config: LLMConfig | None = None, transport: httpx.AsyncBaseTransport | None = None):
        cfg = config or LLMConfig.for_classification()
        try:
            self._client = ChatClient(cfg, transport=transport)
        except LLMNotConfigured as e:
            raise ClassifierError(str(e)) from e
        self.model = cfg.model

    async def ask(self, state: str, questions: dict[str, Question]) -> dict[str, Answer]:
        spec = {qid: _describe(q) for qid, q in questions.items()}
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"STATE:\n{state}\n\nQUESTIONS:\n{json.dumps(spec, indent=1)}"},
        ]
        try:
            data = await self._client.chat_json(messages, temperature=0.0, max_tokens=1500)
        except LLMError as e:
            raise ClassifierError(str(e)) from e
        except httpx.HTTPError as e:
            raise ClassifierError(f"Couldn't reach the model ({type(e).__name__}: {e})") from e
        if not isinstance(data, dict):
            raise ClassifierError("Classifier model returned non-object JSON")
        return {qid: _parse(q, data.get(qid)) for qid, q in questions.items()}
