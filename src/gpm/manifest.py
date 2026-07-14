"""The page manifest.

This module is the reason the row count can never drift. The manifest is built
from `doc.page_count` *before* a single model call is made, and every later
stage is a left-join onto it. There is no code path that appends a row, and no
code path that removes one. Covers, blank pages, advertisements and back matter
get rows by construction -- because rows come from the page count, not from
the content of the page.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

LOW_TEXT_CHARS = 120  # below this, OCR is likely thin -> QA flag
DENSE_TEXT_CHARS = 6000  # above this, a human should probably look -> QA flag


@dataclass(frozen=True)
class PageRecord:
    asset_page: int  # 1-based physical page. Assigned by code, never by model.
    text: str
    char_count: int
    is_low_text: bool
    is_dense: bool


@dataclass(frozen=True)
class PdfManifest:
    pdf_path: Path
    page_count: int
    pages: tuple[PageRecord, ...]

    def __post_init__(self) -> None:
        # The invariant, asserted at construction rather than trusted.
        if len(self.pages) != self.page_count:
            raise RuntimeError(
                f"manifest corrupt: {len(self.pages)} records for "
                f"{self.page_count} pages in {self.pdf_path.name}"
            )


def build_manifest(pdf_path: Path) -> PdfManifest:
    pdf_path = Path(pdf_path)
    with fitz.open(pdf_path) as doc:
        page_count = doc.page_count
        pages = []
        for i in range(page_count):
            # sort=True re-derives reading order for multi-column scans, which
            # historical publications almost always are.
            text = doc[i].get_text("text", sort=True).strip()
            n = len(text)
            pages.append(
                PageRecord(
                    asset_page=i + 1,
                    text=text,
                    char_count=n,
                    is_low_text=n < LOW_TEXT_CHARS,
                    is_dense=n > DENSE_TEXT_CHARS,
                )
            )
    return PdfManifest(pdf_path=pdf_path, page_count=page_count, pages=tuple(pages))
