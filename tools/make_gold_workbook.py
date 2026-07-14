"""A synthetic 'approved workbook' -- what human reviewers signed off on.

Stands in for the client's example approved output. Note where it deliberately
disagrees with what the processor can know on its own:

  * page 3: the humans resolved "Peggy Whitfield" to Margaret Anne (they checked
    the chapter roster by hand). The processor cannot: two Peggys exist. So the
    processor DEFERS. Scoring must call that acceptable, not an error.
  * page 4: the humans read "Sigma" and "Rho" as prose, not chapters. The
    processor flags them rather than exporting them. Also acceptable.
  * page 5: Harold Fenwick is not a member. Neither approves him. The processor
    surfaces him for review -- which is how a roster gap gets found.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from make_synthetic_data import ROOT  # noqa: E402

GOLD = ROOT / "data/gold"

COLUMNS = [
    "Order", "Asset Page #", "Asset Filename Placeholder", "Printed Page #",
    "Page Title", "Names Referenced", "Name Review",
    "Greek Chapter Referenced", "Chapter Review", "Subjects",
    "Suggested Subjects", "Notes for Review",
]

# page -> (printed page #, approved names, approved chapters)
APPROVED = {
    1: ("1", "", ""),
    2: ("2", "Arronte, Mary Louise Ross", "Beta Theta"),
    3: ("3", "Hargrove, Eleanor; Whitfield, James; Whitfield, Margaret Anne", ""),
    4: ("4", "", "Delta Nu"),
    5: ("5", "", ""),
    6: ("", "", ""),
    7: ("", "", ""),
    8: ("8", "", ""),
}


def build(stem: str, pages: int) -> pd.DataFrame:
    rows = []
    for p in range(1, pages + 1):
        printed, names, chapters = APPROVED[p]
        rows.append({
            "Order": p, "Asset Page #": p,
            "Asset Filename Placeholder": f"{stem}_p{p:04d}",
            "Printed Page #": printed, "Page Title": "",
            "Names Referenced": names, "Name Review": "",
            "Greek Chapter Referenced": chapters, "Chapter Review": "",
            "Subjects": "", "Suggested Subjects": "", "Notes for Review": "",
        })
    return pd.DataFrame(rows, columns=COLUMNS)


def main() -> None:
    GOLD.mkdir(parents=True, exist_ok=True)
    for stem, pages in (("quarterly_1971_01", 8), ("quarterly_1971_02", 5)):
        df = build(stem, pages)
        with pd.ExcelWriter(GOLD / f"{stem}_approved.xlsx", engine="openpyxl") as xw:
            df.to_excel(xw, sheet_name="Metadata", index=False)
    print(f"wrote approved workbooks to {GOLD}")


if __name__ == "__main__":
    main()
