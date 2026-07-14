"""Authority files are the single source of truth.

Nothing reaches an output workbook unless it exists here first.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# Diminutives are a *candidate generation* aid only. They widen the net for
# lookup; they never change the exported value, which always comes from the
# authority row itself.
DEFAULT_DIMINUTIVES = {
    "elizabeth": ["liz", "beth", "betty", "eliza", "lizzie"],
    "margaret": ["peggy", "maggie", "marge", "greta"],
    "katherine": ["kate", "kathy", "kay", "kitty"],
    "catherine": ["cate", "kate", "cathy"],
    "mary": ["molly", "mamie", "may"],
    "dorothy": ["dot", "dottie"],
    "frances": ["fran", "frankie"],
    "virginia": ["ginny", "ginger"],
    "eleanor": ["nell", "nellie"],
    "josephine": ["jo", "josie"],
    "patricia": ["pat", "patty", "tricia"],
    "barbara": ["barb", "babs"],
}


def norm(s: object) -> str:
    """Casefold, strip accents and punctuation, collapse whitespace."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip().casefold()


def file_version(path: Path) -> str:
    """Short content hash, recorded in the run log so any output can be
    re-validated against the exact authority file that produced it."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


@dataclass
class Member:
    member_id: str
    formatted_name: str  # THE exported value. Never model-generated.
    first: str = ""
    middle_maiden: str = ""
    last: str = ""
    prefix: str = ""
    suffix: str = ""
    nickname: str = ""
    chapter: str = ""
    initiation_year: str = ""
    birth_year: str = ""
    death_year: str = ""

    def context_line(self) -> str:
        """Compact disambiguation context handed to the model. Deliberately
        small: the model needs enough to *choose*, not enough to *invent*."""
        bits = [f"{self.member_id}: {self.formatted_name}"]
        if self.chapter:
            bits.append(f"chapter={self.chapter}")
        if self.initiation_year:
            bits.append(f"initiated={self.initiation_year}")
        if self.birth_year or self.death_year:
            bits.append(f"life={self.birth_year}-{self.death_year}")
        return " | ".join(bits)


def derive_formatted_name(row: dict) -> str:
    """Spec rule: `Last, Suffix, Prefix First Middle Maiden`.

    Used only when the authority file has no formatted-name column.
    """
    last = str(row.get("last", "") or "").strip()
    suffix = str(row.get("suffix", "") or "").strip()
    prefix = str(row.get("prefix", "") or "").strip()
    first = str(row.get("first", "") or "").strip()
    middle = str(row.get("middle_maiden", "") or "").strip()

    lead = ", ".join([p for p in (last, suffix) if p])
    tail = " ".join([p for p in (prefix, first, middle) if p])
    return f"{lead}, {tail}".strip(", ").strip() if lead else tail


@dataclass
class MemberAuthority:
    members: dict[str, Member]
    index: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    version: str = ""

    @property
    def valid_names(self) -> set[str]:
        """The exact-match whitelist used by the validation gate."""
        return {m.formatted_name for m in self.members.values()}

    def lookup(self, given: str, surname: str) -> set[str]:
        return self.index.get((norm(given), norm(surname)), set())


def _add(index: dict, given: str, surname: str, mid: str) -> None:
    g, s = norm(given), norm(surname)
    if g and s:
        index.setdefault((g, s), set()).add(mid)


def build_member_authority(
    path: Path, nickname_map: dict[str, list[str]] | None = None
) -> MemberAuthority:
    """Load the roster and build the multi-key candidate index.

    A member is reachable through every plausible name form a publication
    might print -- college-era, maiden, married, nickname, initials -- while
    the *exported* value stays pinned to one canonical string.
    """
    df = pd.read_excel(path).fillna("")
    df.columns = [norm(c).replace(" ", "_") for c in df.columns]

    nickname_map = nickname_map or {}
    members: dict[str, Member] = {}
    index: dict[tuple[str, str], set[str]] = defaultdict(set)

    for i, raw in df.iterrows():
        row = {k: str(v).strip() for k, v in raw.items()}
        mid = row.get("member_id") or f"M{i:05d}"

        formatted = row.get("formatted_name") or derive_formatted_name(row)
        if not formatted:
            continue

        m = Member(
            member_id=mid,
            formatted_name=formatted,
            first=row.get("first", ""),
            middle_maiden=row.get("middle_maiden", ""),
            last=row.get("last", ""),
            prefix=row.get("prefix", ""),
            suffix=row.get("suffix", ""),
            nickname=row.get("nickname", ""),
            chapter=row.get("chapter", ""),
            initiation_year=row.get("initiation_year", ""),
            birth_year=row.get("birth_year", ""),
            death_year=row.get("death_year", ""),
        )
        members[mid] = m

        # --- given-name forms -------------------------------------------------
        givens = {m.first, m.nickname}
        givens |= set(nickname_map.get(norm(m.first), []))
        givens |= set(DEFAULT_DIMINUTIVES.get(norm(m.first), []))
        if m.first:
            givens.add(m.first[0])  # "M. Arronte"
        givens = {g for g in givens if g}

        # --- surname forms ----------------------------------------------------
        # The maiden field may hold multiple tokens ("Louise Ross"): each token
        # is independently a surname candidate. This is what makes the spec's
        # Mary Louise Ross -> Arronte example resolve.
        surnames = {m.last}
        if m.middle_maiden:
            surnames.add(m.middle_maiden)
            surnames.update(m.middle_maiden.split())
            if m.last:
                surnames.add(f"{m.middle_maiden} {m.last}")
        surnames = {s for s in surnames if s}

        for g in givens:
            for s in surnames:
                _add(index, g, s, mid)

    return MemberAuthority(
        members=members, index=dict(index), version=file_version(path)
    )


@dataclass
class ChapterAuthority:
    chapters: dict[str, str]  # chapter_id -> canonical name
    version: str = ""

    @property
    def valid_chapters(self) -> set[str]:
        return set(self.chapters.values())


def build_chapter_authority(path: Path) -> ChapterAuthority:
    df = pd.read_excel(path).fillna("")
    df.columns = [norm(c).replace(" ", "_") for c in df.columns]
    chapters = {}
    for i, raw in df.iterrows():
        row = {k: str(v).strip() for k, v in raw.items()}
        name = row.get("chapter_name") or row.get("chapter") or ""
        if name:
            chapters[row.get("chapter_id") or f"C{i:04d}"] = name
    return ChapterAuthority(chapters=chapters, version=file_version(path))
