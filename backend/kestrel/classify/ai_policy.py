"""Detects employers that ask applicants not to use AI tools.

Checked against the job description *and* the application form (many employers put the policy in
an attestation question rather than the posting). A deterministic keyword check always runs
alongside the classifier, so the safeguard never depends on a model being available or right.
"""

from __future__ import annotations

import re

from kestrel.classify.base import Classifier, ClassifierError, Noul, NoulAnswer

THRESHOLD = 0.5
MAX_CHARS = 12000

AI_POLICY_QUESTION = Noul(
    "The text asks applicants not to use AI tools (such as ChatGPT or other generative AI) to write "
    "their application, answers, materials or assessments, or requires them to attest they did not.",
)

_AI = r"(?:generative\s+)?(?:a\.?i\.?|artificial intelligence|chatgpt|gpt|llms?|large language models?)(?:\s+tools?)?"
_PATTERNS = [
    re.compile(
        rf"\b(?:use of|using|use)\s+(?:any\s+)?{_AI}\b[^.]{{0,200}}"
        r"\b(?:prohibited|not permitted|not allowed|forbidden|disqualif)",
        re.I,
    ),
    re.compile(
        r"\b(?:prohibited|not permitted|not allowed|forbidden|do not|don't|must not|may not)\b[^.]{0,80}"
        rf"\b(?:use|using)\b[^.]{{0,40}}\b{_AI}\b",
        re.I,
    ),
    re.compile(rf"\bwithout (?:the )?(?:use of|using|assistance (?:of|from))\s+(?:any\s+)?{_AI}\b", re.I),
    re.compile(rf"\b(?:no|not)\s+{_AI}[- ]?(?:generated|written|assisted)\b", re.I),
]


def keyword_score(text: str) -> float:
    return 1.0 if any(p.search(text) for p in _PATTERNS) else 0.0


async def ai_policy_score(classifier: Classifier | None, *texts: str) -> float:
    combined = "\n\n".join(t for t in texts if t)[:MAX_CHARS]
    if not combined.strip():
        return 0.0
    score = keyword_score(combined)
    if score >= THRESHOLD or classifier is None:
        return score
    try:
        ans = (await classifier.ask(combined, {"ai": AI_POLICY_QUESTION}))["ai"]
    except ClassifierError:
        return score
    return max(score, ans.value if isinstance(ans, NoulAnswer) else 0.0)
