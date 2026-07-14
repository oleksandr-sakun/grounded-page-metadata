"""The model layer.

The model returns *identifiers chosen from an enum*, never names. The enum is
rebuilt per page from that page's candidate set. A hallucinated id is not a
subtle error that slips into a workbook -- it is a schema violation the API
itself rejects.

The model's actual jobs: page title, printed page number, contextual
disambiguation among candidates, subjects, review notes. It does not own row
count, final values, or validation.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from .candidates import ChapterCandidate, NameCandidate
from .manifest import PageRecord


class PageInterpretation(BaseModel):
    page_title: str = Field(description="Title as printed, or a short description")
    printed_page_number: str = Field(default="", description="Empty if not visible")
    member_ids: list[str] = Field(default_factory=list)
    chapter_ids: list[str] = Field(default_factory=list)
    ambiguous_member_ids: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    suggested_subjects: list[str] = Field(default_factory=list)
    notes_for_review: str = ""


def build_schema(
    names: list[NameCandidate],
    chapters: list[ChapterCandidate],
    taxonomy: list[str] | None,
) -> dict:
    """Strict JSON Schema, with the candidate ids injected as enums.

    This function is the load-bearing wall of the whole system.
    """
    member_ids = sorted({c.member_id for c in names})
    chapter_ids = sorted({c.chapter_id for c in chapters})

    def id_array(allowed: list[str]) -> dict:
        # An empty enum is invalid JSON Schema, so with no candidates we emit a
        # type that admits nothing but the empty array. The model literally
        # cannot name a person on a page where code found no candidate.
        if not allowed:
            return {"type": "array", "items": False, "maxItems": 0}
        return {"type": "array", "items": {"type": "string", "enum": allowed}}

    subjects = (
        {"type": "array", "items": {"type": "string", "enum": taxonomy}}
        if taxonomy
        else {"type": "array", "items": {"type": "string"}}
    )

    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "page_title", "printed_page_number", "member_ids", "chapter_ids",
            "ambiguous_member_ids", "subjects", "suggested_subjects",
            "notes_for_review",
        ],
        "properties": {
            "page_title": {"type": "string"},
            "printed_page_number": {"type": "string"},
            "member_ids": id_array(member_ids),
            "chapter_ids": id_array(chapter_ids),
            "ambiguous_member_ids": id_array(member_ids),
            "subjects": subjects,
            "suggested_subjects": {"type": "array", "items": {"type": "string"}},
            "notes_for_review": {"type": "string"},
        },
    }


@dataclass
class ModelResult:
    interpretation: PageInterpretation | None
    error: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""


class StubModel:
    """Offline model. Deterministic, free, and good enough to prove the plumbing.

    Lets anyone clone the repo and get a full run with no API key -- and lets
    the test suite assert the no-fabrication property without a network call.
    """

    name = "stub"

    def interpret(self, page: PageRecord, names, chapters, taxonomy, prompt) -> ModelResult:
        first_line = next(
            (ln.strip() for ln in page.text.splitlines() if ln.strip()), ""
        )
        printed = ""
        if m := re.search(r"(?:^|\n)\s*(?:page\s+)?(\d{1,4})\s*$", page.text, re.I):
            printed = m.group(1)

        by_text: dict[str, list] = {}
        for c in names:
            by_text.setdefault(c.observed_text, []).append(c)

        confident, ambiguous = [], []
        for group in by_text.values():
            # One observed string, two members -> a guess would be a coin flip.
            # We refuse to flip. It goes to a human.
            (confident if len(group) == 1 else ambiguous).append(group[0].member_id)

        chapter_ids = [
            c.chapter_id
            for c in chapters
            if not c.single_letter or c.has_context_cue
        ]
        return ModelResult(
            interpretation=PageInterpretation(
                page_title=first_line[:120] or f"[untitled page {page.asset_page}]",
                printed_page_number=printed,
                member_ids=confident,
                chapter_ids=chapter_ids,
                ambiguous_member_ids=ambiguous,
                subjects=[],
                suggested_subjects=[],
                notes_for_review="stub model: no semantic interpretation",
            ),
            model=self.name,
        )


class OpenAIModel:
    def __init__(self, model: str = "gpt-4o-mini") -> None:
        from openai import OpenAI  # imported lazily: repo runs without the SDK

        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.name = model

    def interpret(self, page: PageRecord, names, chapters, taxonomy, prompt) -> ModelResult:
        schema = build_schema(names, chapters, taxonomy)
        roster = "\n".join(f"- {c.member_id} | {c.observed_text!r} -> {c.context_line}"
                           for c in names) or "- (none)"
        chap = "\n".join(f"- {c.chapter_id} | {c.chapter_name}"
                         for c in chapters) or "- (none)"

        user = (
            f"PAGE {page.asset_page} OCR TEXT\n---\n{page.text[:12000]}\n---\n\n"
            f"CANDIDATE MEMBERS (choose only from these ids):\n{roster}\n\n"
            f"CANDIDATE CHAPTERS (choose only from these ids):\n{chap}\n"
        )
        try:
            r = self.client.chat.completions.create(
                model=self.name,
                temperature=0,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "page_interpretation",
                        "strict": True,
                        "schema": schema,
                    },
                },
            )
            data = json.loads(r.choices[0].message.content)
            return ModelResult(
                interpretation=PageInterpretation(**data),
                tokens_in=r.usage.prompt_tokens,
                tokens_out=r.usage.completion_tokens,
                model=self.name,
            )
        except Exception as e:  # noqa: BLE001
            # A failed call must never delete a row. It produces a flagged row.
            return ModelResult(interpretation=None, error=f"{type(e).__name__}: {e}",
                               model=self.name)
