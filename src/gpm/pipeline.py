from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .authority import ChapterAuthority, MemberAuthority
from .candidates import (
    chapter_candidates, name_candidates, observed_person_spans,
)
from .export import METADATA_COLUMNS, batch_summary, write_workbook
from .manifest import build_manifest
from .validate import validate_page


class RowCountMismatch(RuntimeError):
    """Raised instead of writing a workbook. A wrong workbook is worse than none."""


@dataclass
class Run:
    members: MemberAuthority
    chapters: ChapterAuthority
    model: object
    prompt: str
    taxonomy: list[str] | None
    out_dir: Path

    def process_pdf(self, pdf_path: Path) -> dict:
        started = datetime.now(timezone.utc)
        manifest = build_manifest(pdf_path)
        stem = pdf_path.stem

        rows, n_audit, n_review, c_audit, c_review, qa = [], [], [], [], [], []
        tok_in = tok_out = 0

        for page in manifest.pages:
            names = name_candidates(page.text, self.members)
            chaps = chapter_candidates(page.text, self.chapters)
            spans = observed_person_spans(
                page.text, set(self.chapters.chapters.values())
            )
            res = self.model.interpret(page, names, chaps, self.taxonomy, self.prompt)
            tok_in += res.tokens_in
            tok_out += res.tokens_out

            pr = validate_page(page, res, names, chaps, spans, self.members,
                               self.chapters, stem, self.taxonomy)
            rows.append(pr.row)
            n_audit += pr.name_audit
            n_review += pr.name_review
            c_audit += pr.chapter_audit
            c_review += pr.chapter_review
            qa += pr.qa_flags

        # ---- THE INVARIANT -------------------------------------------------
        if len(rows) != manifest.page_count:
            raise RowCountMismatch(
                f"{stem}: {len(rows)} rows for {manifest.page_count} pages"
            )
        meta = pd.DataFrame(rows, columns=METADATA_COLUMNS)
        assert list(meta["Asset Page #"]) == list(range(1, manifest.page_count + 1))
        # --------------------------------------------------------------------

        log = {
            "pdf_filename": pdf_path.name,
            "page_count": manifest.page_count,
            "rows_exported": len(meta),
            "model": getattr(self.model, "name", "unknown"),
            "prompt_version": _hash(self.prompt),
            "member_authority_version": self.members.version,
            "chapter_authority_version": self.chapters.version,
            "tokens_in": tok_in,
            "tokens_out": tok_out,
            "started_utc": started.isoformat(),
            "finished_utc": datetime.now(timezone.utc).isoformat(),
        }

        frames = {
            "Metadata": meta,
            "Name_Match_Audit": pd.DataFrame(n_audit),
            "Name_Review_Detail": pd.DataFrame(n_review),
            "Chapter_Audit": pd.DataFrame(c_audit),
            "Chapter_Review_Detail": pd.DataFrame(c_review),
            "QA_Flags": pd.DataFrame(qa),
            "Run_Notes": pd.DataFrame([log]),
        }

        wb = self.out_dir / f"{stem}_metadata_draft.xlsx"
        write_workbook(wb, frames)
        meta.to_csv(self.out_dir / f"{stem}_metadata_draft.csv", index=False)
        (self.out_dir / f"{stem}_processing_log.json").write_text(
            json.dumps(log, indent=2)
        )

        flags = pd.DataFrame(qa)
        def n(code: str) -> int:
            return 0 if flags.empty else int((flags["Flag"] == code).sum())

        return {
            "Source PDF filename": pdf_path.name,
            "Detected page count": manifest.page_count,
            "Metadata rows exported": len(meta),
            "Name Review count": n("NAME_REVIEW"),
            "Chapter Review count": n("CHAPTER_REVIEW") + n("SINGLE_LETTER_CHAPTER"),
            "Dense page flags": n("DENSE_PAGE"),
            "Invalid name count": n("INVALID_NAME") + n("INVALID_MEMBER_ID"),
            "Invalid chapter count": n("INVALID_CHAPTER") + n("INVALID_CHAPTER_ID"),
            "Output workbook path": str(wb),
            "QA status": "REVIEW" if len(flags) else "CLEAN",
            "Notes": f"{tok_in + tok_out} tokens",
        }

    def process_folder(self, folder: Path) -> pd.DataFrame:
        pdfs = sorted(Path(folder).glob("*.pdf"))
        if not pdfs:
            raise FileNotFoundError(f"no PDFs in {folder}")
        summary = [self.process_pdf(p) for p in pdfs]
        return batch_summary(summary, self.out_dir / "batch_summary.xlsx")


def _hash(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()[:12]
