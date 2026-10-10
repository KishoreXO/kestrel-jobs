"""Jev by TypeSafe — the recommended classifier.

Jev is purpose-built for typed judgements (Noul/Choice/Score) and returns calibrated
probability distributions, which is exactly what the matching pipeline needs. It is much
faster and cheaper per decision than prompting a general chat model.

API: POST https://api.typesafe.ai/v1/systemone (docs: https://docs.typesafe.ai/api).
"""

from __future__ import annotations

import asyncio

import httpx

from kestrel.classify.base import (
    Answer,
    Choice,
    ChoiceAnswer,
    ClassifierError,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
)
from kestrel.classify.questions import SELECTION_RULE
from kestrel.config import get_settings

ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def _question_payload(q: Question) -> dict:
    if isinstance(q, Noul):
        payload: dict = {"type": "noul", "instructions": q.instructions}
        criteria = {k: v for k, v in (("true", q.true), ("false", q.false)) if v}
        if criteria:
            payload["criteria"] = criteria
        return payload
    if isinstance(q, Choice):
        return {"type": "choice", "instructions": q.instructions, "criteria": q.criteria}
    if isinstance(q, Score):
        return {"type": "score", "instructions": f"{q.instructions} {SELECTION_RULE}", "criteria": q.levels}
    raise TypeError(f"Unknown question type: {type(q)!r}")


def _parse_answer(q: Question, raw: dict) -> Answer:
    if isinstance(q, Noul):
        return NoulAnswer(value=float(raw.get("noul", 0.0)))
    if isinstance(q, Choice):
        return ChoiceAnswer(
            choice=str(raw.get("choice", "")),
            probabilities={str(k): float(v) for k, v in (raw.get("probabilities") or {}).items()},
            confidence=float(raw.get("confidence", 0.0)),
        )
    # Score: "score" is the probability-weighted level; the legend maps level numbers to text.
    legend = raw.get("legend") or {}
    level_keys = sorted(int(k) for k in legend) if legend else list(range(len(q.levels)))
    lo, hi = (level_keys[0], level_keys[-1]) if level_keys else (0, max(1, len(q.levels) - 1))
    score = float(raw.get("score", lo))
    value = (score - lo) / (hi - lo) if hi > lo else 0.0
    return ScoreAnswer(
        value=min(1.0, max(0.0, value)),
        probabilities={str(k): float(v) for k, v in (raw.get("probabilities") or {}).items()},
        confidence=float(raw.get("confidence", 0.0)),
    )


class JevClassifier:
    name = "jev"

    def __init__(
        self, *, api_key: str | None = None, model: str | None = None, transport: httpx.AsyncBaseTransport | None = None
    ):
        s = get_settings()
        self.api_key = api_key if api_key is not None else s.typesafe_api_key
        self.model = model or s.jev_model
        self._transport = transport
        if not self.api_key:
            raise ClassifierError("Jev needs a TypeSafe API key (Settings → Classifier).")

    async def ask(self, state: str, questions: dict[str, Question]) -> dict[str, Answer]:
        body = {
            "model": self.model,
            "state": state,
            "questions": {k: _question_payload(q) for k, q in questions.items()},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            data = await self._post(body, headers)
        except httpx.HTTPError as e:
            raise ClassifierError(f"Couldn't reach Jev ({type(e).__name__}: {e})") from e
        except ValueError as e:
            raise ClassifierError("Jev returned a response that isn't JSON") from e

        if not isinstance(data, dict) or not isinstance(data.get("answers") or {}, dict):
            raise ClassifierError("Jev returned a response Kestrel couldn't read")
        if data.get("model"):
            self.model = str(data["model"])
        answers = data.get("answers") or {}
        missing = set(questions) - set(answers)
        if missing:
            raise ClassifierError(f"Jev response missing answers for: {', '.join(sorted(missing))}")
        try:
            return {k: _parse_answer(q, answers[k]) for k, q in questions.items()}
        except (TypeError, ValueError, AttributeError) as e:
            raise ClassifierError(f"Jev returned an answer Kestrel couldn't read ({type(e).__name__})") from e

    async def _post(self, body: dict, headers: dict) -> dict:
        async with httpx.AsyncClient(timeout=60, transport=self._transport) as client:
            for attempt in range(5):
                resp = await client.post(ENDPOINT, json=body, headers=headers)
                if resp.status_code in (429, 529, 502, 503) and attempt < 4:
                    retry_after = resp.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                    await asyncio.sleep(min(delay, 30))
                    continue
                if resp.status_code == 401:
                    raise ClassifierError("TypeSafe rejected the API key (401).")
                if resp.status_code >= 400:
                    raise ClassifierError(f"Jev returned {resp.status_code}: {resp.text[:300]}")
                return resp.json()
        raise ClassifierError("Jev kept returning overload errors")
