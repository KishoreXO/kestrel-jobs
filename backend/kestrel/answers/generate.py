"""Grounded answer suggestions. The user reviews, edits and copies them; nothing is ever submitted."""

from __future__ import annotations

import json

from kestrel.answers.questions import SKIPPED_KINDS, FormQuestion
from kestrel.llm.provider import ChatClient, LLMConfig
from kestrel.models import Job
from kestrel.profile.schema import CandidateProfile

SYSTEM = """You help a job seeker prepare answers to an employer's application form.
Hard rules:
1. Use ONLY facts present in CANDIDATE_PROFILE. Never invent or embellish employers, dates, titles,
   numbers, skills, degrees, certifications, achievements or eligibility.
2. If a needed fact is missing, write a placeholder like [fill in: years of Kubernetes experience]
   instead of guessing. Never answer a yes/no eligibility question (work authorization,
   sponsorship, clearance, relocation, background check) unless the profile states it.
3. For single-select questions, answer with one of the given options verbatim, or a placeholder.
4. Be honest, specific and concise, in the candidate's first-person voice. Plain text, no markdown.
   Long-form answers: 80-180 words unless the question asks otherwise.
5. Tailor to the JOB_POSTING only where the profile genuinely supports it.
Return JSON: {"answers": [{"id": <question id>, "answer": "<text>", "facts_used": ["<profile fact>", ...]}]}"""


async def draft_answers(
    profile: CandidateProfile, job: Job, questions: list[FormQuestion], client: ChatClient | None = None
) -> list[dict]:
    results: list[dict] = []
    to_draft = []
    for i, q in enumerate(questions):
        if q.kind in SKIPPED_KINDS:
            results.append(
                {
                    "id": i,
                    "question": q.label,
                    "kind": q.kind,
                    "answer": "",
                    "note": SKIPPED_KINDS[q.kind],
                    "skipped": True,
                }
            )
        else:
            to_draft.append(
                {
                    "id": i,
                    "question": q.label,
                    "kind": q.kind,
                    "required": q.required,
                    "options": q.options or None,
                    "help_text": q.description or None,
                }
            )
    if to_draft:
        client = client or ChatClient(LLMConfig.for_drafting())
        payload = {
            "CANDIDATE_PROFILE": profile.model_dump(exclude_defaults=True),
            "JOB_POSTING": {
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "description": job.description[:10000],
            },
            "QUESTIONS": [{k: v for k, v in q.items() if v is not None} for q in to_draft],
        }
        data = await client.chat_json(
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.3,
            max_tokens=4000,
        )
        drafted = {}
        for item in (data.get("answers") if isinstance(data, dict) else data) or []:
            if isinstance(item, dict) and "id" in item:
                try:
                    drafted[int(item["id"])] = item
                except (TypeError, ValueError):
                    continue
        for q in to_draft:
            item = drafted.get(q["id"], {})
            answer = str(item.get("answer", "")).strip() or "[fill in]"
            facts = item.get("facts_used") or []
            note = "Contains placeholders you must complete." if "[fill in" in answer else ""
            results.append(
                {
                    "id": q["id"],
                    "question": q["question"],
                    "kind": q["kind"],
                    "answer": answer,
                    "facts_used": facts if isinstance(facts, list) else [],
                    "note": note,
                    "skipped": False,
                }
            )
    return sorted(results, key=lambda r: r["id"])
