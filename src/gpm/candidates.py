"""Candidate generation: the retrieval step the model is NOT allowed to do.

The roster is never sent to the model. Code extracts person-like spans from the
page, resolves them against the authority key index, and hands the model a
short candidate set. Three consequences:

  * fabrication becomes structurally impossible, not merely discouraged
  * cost per page is flat regardless of roster size (Phase 2 economics)
  * every match has a traceable rule path for the audit sheet
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .authority import ChapterAuthority, MemberAuthority, norm

GREEK = [
    "Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta",
    "Iota", "Kappa", "Lambda", "Mu", "Nu", "Xi", "Omicron", "Pi", "Rho",
    "Sigma", "Tau", "Upsilon", "Phi", "Chi", "Psi", "Omega",
]

# A capitalised run of 2-4 tokens, allowing initials ("M. L. Ross") and
# lowercase nobiliary particles ("van der Berg").
PERSON_SPAN = re.compile(
    r"\b(?:[A-Z][a-z]+|[A-Z]\.)"
    r"(?:\s+(?:[A-Z][a-z]+|[A-Z]\.|van|von|de|del|della|di|la|le))"
    r"{1,3}\b"
)

# Deliberately tight. An earlier version accepted "at the ..." as context and
# duly exported a bare "Sigma" as a chapter -- precisely the false positive the
# spec warns about. For a single-letter chapter, only an explicit chapter word
# adjacent to the mention counts.
CHAPTER_CUE = re.compile(r"\b(chapter|colony|charter|installed)\b", re.I)
CUE_WINDOW = 40

# Tokens that make a capitalised span not-a-person.
NOT_A_PERSON = {
    "the", "a", "an", "in", "of", "and", "volume", "number", "spring", "summer",
    "autumn", "winter", "fall", "chapter", "news", "notes", "members", "fine",
    "established", "sons", "quarterly", "society", "committee", "board",
}


@dataclass
class NameCandidate:
    observed_text: str
    member_id: str
    formatted_name: str
    match_path: str  # e.g. "First+Maiden" -- goes straight into the audit sheet
    context_line: str


@dataclass
class ChapterCandidate:
    observed_text: str
    chapter_id: str
    chapter_name: str
    single_letter: bool
    has_context_cue: bool


def _match_path(given: str, surname: str, member) -> str:
    g, s = norm(given), norm(surname)
    gk = "First" if g == norm(member.first) else (
        "Nickname" if g == norm(member.nickname) else (
            "Initial" if len(g) == 1 else "Variant"))
    if s == norm(member.last):
        sk = "Last/Married"
    elif s == norm(member.middle_maiden):
        sk = "Middle/Maiden"
    elif member.middle_maiden and s in {norm(t) for t in member.middle_maiden.split()}:
        sk = "Maiden"
    elif member.middle_maiden and member.last and s == norm(
        f"{member.middle_maiden} {member.last}"
    ):
        sk = "Maiden+Married"
    else:
        sk = "Variant"
    return f"{gk}+{sk}"


def name_candidates(text: str, authority: MemberAuthority) -> list[NameCandidate]:
    """Every (given, surname) pairing inside each observed span is probed."""
    out: list[NameCandidate] = []
    seen: set[tuple[str, str]] = set()

    for span in {m.group(0) for m in PERSON_SPAN.finditer(text)}:
        tokens = [t for t in span.split() if t]
        for gi in range(len(tokens) - 1):
            for si in range(gi + 1, len(tokens)):
                given, surname = tokens[gi], tokens[si]
                for mid in authority.lookup(given.rstrip("."), surname):
                    if (span, mid) in seen:
                        continue
                    seen.add((span, mid))
                    m = authority.members[mid]
                    out.append(
                        NameCandidate(
                            observed_text=span,
                            member_id=mid,
                            formatted_name=m.formatted_name,
                            match_path=_match_path(given.rstrip("."), surname, m),
                            context_line=m.context_line(),
                        )
                    )
    return out


def chapter_candidates(
    text: str, authority: ChapterAuthority
) -> list[ChapterCandidate]:
    """Chapters are matched ONLY on explicit mention in the page text.

    A chapter is never inferred from a member's authority record -- that is the
    single most tempting shortcut in this domain and the spec forbids it.
    """
    out: list[ChapterCandidate] = []
    lowered = text.casefold()

    for cid, cname in authority.chapters.items():
        pattern = re.compile(rf"\b{re.escape(cname.casefold())}\b")
        m = pattern.search(lowered)
        if not m:
            continue
        window = text[max(0, m.start() - CUE_WINDOW): m.end() + CUE_WINDOW]
        out.append(
            ChapterCandidate(
                observed_text=cname,
                chapter_id=cid,
                chapter_name=cname,
                single_letter=cname.strip() in GREEK,  # "Sigma" alone is ambiguous
                has_context_cue=bool(CHAPTER_CUE.search(window)),
            )
        )
    return out


def observed_person_spans(text: str, chapter_names: set[str]) -> set[str]:
    """Every capitalised span that plausibly names a person.

    Used for RECALL, not for export. A span here that resolves to no authority
    member is not discarded -- it is surfaced in Name Review as a possible gap
    in the roster. Silence would be the more dangerous outcome.
    """
    out = set()
    greek = {g.casefold() for g in GREEK}
    chapters_cf = {c.casefold() for c in chapter_names}

    for m in PERSON_SPAN.finditer(text):
        span = m.group(0).strip()
        if span.casefold() in chapters_cf:
            continue
        tokens = [t.strip(".").casefold() for t in span.split()]
        if any(t in greek or t in NOT_A_PERSON for t in tokens):
            continue
        out.add(span)
    return out
