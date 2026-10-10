"""Typed classification primitives shared by every classifier backend.

The primitives mirror TypeSafe's System One API: a *state* (the content being judged) plus
named, narrow *questions*, each answered with a typed result:

- Noul   – how true a statement is, 0..1
- Choice – one option from a fixed set, with a probability per option
- Score  – a level on an ordered rubric, with a probability per level
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Noul:
    instructions: str
    true: str = ""
    false: str = ""


@dataclass(frozen=True)
class Choice:
    instructions: str
    criteria: dict[str, str]


@dataclass(frozen=True)
class Score:
    instructions: str
    levels: list[str]


Question = Noul | Choice | Score


@dataclass
class NoulAnswer:
    value: float


@dataclass
class ChoiceAnswer:
    choice: str
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass
class ScoreAnswer:
    # Normalised to 0..1 across the rubric (lowest level = 0, highest = 1).
    value: float
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0


Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer


class ClassifierError(Exception):
    pass


class Classifier(Protocol):
    name: str
    model: str

    async def ask(self, state: str, questions: dict[str, Question]) -> dict[str, Answer]: ...


def get_classifier() -> Classifier:
    from kestrel.config import get_settings

    if get_settings().classifier == "jev":
        from kestrel.classify.jev import JevClassifier

        return JevClassifier()
    from kestrel.classify.llm_backend import LLMClassifier

    return LLMClassifier()
