"""Score the processor against a human-approved workbook.

The central idea: **not all errors are equal**, and a single F1 number hides the
only distinction that matters in an archive.

    fabrication  — a name we exported that the humans did not approve.
                   Expensive. It enters a permanent record wearing a badge of
                   authority. This number must be zero.

    silent miss  — a name the humans approved that we neither exported nor
                   flagged. Also expensive, and worse than it looks: the page
                   comes back clean, so nobody ever revisits it.

    deferred     — a name we did not export but DID route to Name Review.
                   Cheap. A human spends thirty seconds. The system behaved
                   exactly as designed.

Precision and recall alone would score a deferred miss and a silent miss
identically. They are not remotely the same failure. So we report `coverage` =
(exported correctly + deferred) / approved, alongside precision.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .export import _autoformat

SEP = "; "


def _cells(series: pd.Series) -> list[set[str]]:
    return [
        {v.strip() for v in str(cell).split(SEP) if v.strip() and v.strip() != "nan"}
        for cell in series.fillna("")
    ]


@dataclass
class FieldScore:
    field: str
    approved: int = 0
    exported_correct: int = 0
    fabricated: int = 0
    deferred: int = 0
    silent_miss: int = 0

    @property
    def precision(self) -> float:
        got = self.exported_correct + self.fabricated
        return self.exported_correct / got if got else 1.0

    @property
    def recall(self) -> float:
        return self.exported_correct / self.approved if self.approved else 1.0

    @property
    def coverage(self) -> float:
        """Approved values either exported correctly or handed to a human.
        The complement of this is data we lost in silence."""
        if not self.approved:
            return 1.0
        return (self.exported_correct + self.deferred) / self.approved


def score_field(
    produced: pd.DataFrame,
    gold: pd.DataFrame,
    final_col: str,
    review_col: str,
    field: str,
) -> tuple[FieldScore, list[dict]]:
    s = FieldScore(field=field)
    diffs: list[dict] = []

    p_final = _cells(produced[final_col])
    p_review = _cells(produced[review_col])
    g_final = _cells(gold[final_col])
    pages = list(produced["Asset Page #"])

    for page, pf, pr, gf in zip(pages, p_final, p_review, g_final):
        s.approved += len(gf)
        s.exported_correct += len(pf & gf)

        for name in sorted(pf - gf):
            s.fabricated += 1
            diffs.append({"Asset Page #": page, "Field": field, "Value": name,
                          "Verdict": "FABRICATION", "Severity": "critical"})

        for name in sorted(gf - pf):
            # Did we at least tell a human to look? The review cell holds the
            # *observed page text*, not the authority name, so a substring test
            # is the honest check here.
            flagged = any(
                tok and tok.casefold() in " ".join(pr).casefold()
                for tok in name.replace(",", " ").split()
                if len(tok) > 3
            )
            if flagged:
                s.deferred += 1
                diffs.append({"Asset Page #": page, "Field": field, "Value": name,
                              "Verdict": "DEFERRED_TO_REVIEW", "Severity": "ok"})
            else:
                s.silent_miss += 1
                diffs.append({"Asset Page #": page, "Field": field, "Value": name,
                              "Verdict": "SILENT_MISS", "Severity": "high"})

    return s, diffs


def score_pdf(produced_xlsx: Path, gold_xlsx: Path) -> tuple[dict, list[dict]]:
    p = pd.read_excel(produced_xlsx, sheet_name="Metadata").fillna("")
    g = pd.read_excel(gold_xlsx, sheet_name="Metadata").fillna("")

    parity = len(p) == len(g)
    if not parity:
        return (
            {"Source": produced_xlsx.stem, "Row parity": False,
             "Produced rows": len(p), "Approved rows": len(g)},
            [{"Asset Page #": "", "Field": "ROW_COUNT", "Value": f"{len(p)} vs {len(g)}",
              "Verdict": "ROW_COUNT_MISMATCH", "Severity": "critical"}],
        )

    names, ndiff = score_field(p, g, "Names Referenced", "Name Review", "name")
    chaps, cdiff = score_field(
        p, g, "Greek Chapter Referenced", "Chapter Review", "chapter"
    )

    printed_ok = int(
        (p["Printed Page #"].astype(str).str.strip()
         == g["Printed Page #"].astype(str).str.strip()).sum()
    )

    row = {
        "Source": produced_xlsx.stem,
        "Row parity": True,
        "Pages": len(p),
        "Printed page # exact": f"{printed_ok}/{len(p)}",
        "Names approved": names.approved,
        "Names correct": names.exported_correct,
        "Name fabrications": names.fabricated,
        "Name deferred": names.deferred,
        "Name silent misses": names.silent_miss,
        "Name precision": round(names.precision, 3),
        "Name recall": round(names.recall, 3),
        "Name coverage": round(names.coverage, 3),
        "Chapter fabrications": chaps.fabricated,
        "Chapter silent misses": chaps.silent_miss,
        "Chapter precision": round(chaps.precision, 3),
        "Chapter coverage": round(chaps.coverage, 3),
        "Verdict": (
            "FAIL" if names.fabricated or chaps.fabricated
            else "REVIEW" if names.silent_miss or chaps.silent_miss
            else "PASS"
        ),
    }
    return row, ndiff + cdiff


def main() -> None:
    ap = argparse.ArgumentParser(prog="gpm.score")
    ap.add_argument("--produced", type=Path, default=Path("data/output"))
    ap.add_argument("--gold", type=Path, default=Path("data/gold"))
    ap.add_argument("--out", type=Path, default=Path("data/output/accuracy_report.xlsx"))
    a = ap.parse_args()

    rows, diffs = [], []
    for gold in sorted(a.gold.glob("*_approved.xlsx")):
        stem = gold.stem.replace("_approved", "")
        produced = a.produced / f"{stem}_metadata_draft.xlsx"
        if not produced.exists():
            print(f"skip {stem}: no produced workbook")
            continue
        row, d = score_pdf(produced, gold)
        rows.append(row)
        diffs.extend({**x, "Source": stem} for x in d)

    if not rows:
        raise SystemExit("nothing to score")

    summary = pd.DataFrame(rows)
    diff_df = pd.DataFrame(diffs)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(a.out, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="Summary", index=False)
        _autoformat(xw.sheets["Summary"])
        (diff_df if not diff_df.empty else pd.DataFrame(columns=["(no diffs)"])
         ).to_excel(xw, sheet_name="Diffs", index=False)
        _autoformat(xw.sheets["Diffs"])

    fab = int(summary["Name fabrications"].sum() + summary["Chapter fabrications"].sum())
    silent = int(
        summary["Name silent misses"].sum() + summary["Chapter silent misses"].sum()
    )

    print(summary.to_string(index=False))
    print()
    print(f"fabrications : {fab}   <- must be 0")
    print(f"silent misses: {silent}   <- data lost without a flag")
    print(f"report       : {a.out}")

    if fab:
        raise SystemExit(1)   # a fabrication fails the build. Non-negotiable.


if __name__ == "__main__":
    main()
