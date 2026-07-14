"""Generate a fake corpus so anyone can clone and run this repo immediately.

Deliberately seeded with the hard cases the real domain throws at you:
  * a maiden-name reference (Mary Louise Ross -> Arronte, Mary Louise Ross)
  * a nickname reference (Peggy Whitfield -> Whitfield, Margaret Anne)
  * an ambiguous surname shared by two members (must go to review, not a guess)
  * a bare Greek letter with no chapter context (must be flagged)
  * a blank page and an advertisement page (must still get rows)
  * an off-roster person (must land in Name Review, never in Names Referenced)
"""
from __future__ import annotations

from pathlib import Path

import fitz
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "data/authority"
IN = ROOT / "data/input"

MEMBERS = [
    # member_id, first, middle_maiden, last, nickname, chapter, init, birth, death
    ("M00001", "Mary", "Louise Ross", "Arronte", "", "Beta Theta", "1948", "1928", "2011"),
    ("M00002", "Margaret", "Anne", "Whitfield", "Peggy", "Gamma Delta", "1951", "1931", ""),
    ("M00003", "Eleanor", "", "Hargrove", "Nell", "Beta Theta", "1949", "1929", "2003"),
    ("M00004", "Katherine", "Reed", "Callahan", "Kitty", "Alpha Omega", "1953", "1933", ""),
    ("M00005", "James", "", "Whitfield", "", "Gamma Delta", "1950", "1930", "1998"),
    ("M00006", "Dorothy", "Vance", "Pemberton", "Dot", "Sigma Chi", "1947", "1927", "2015"),
    ("M00007", "Robert", "", "Ashford", "", "Alpha Omega", "1952", "1932", ""),
    ("M00008", "Frances", "Blair", "Ashford", "Fran", "Sigma Chi", "1954", "1934", ""),
    # The collision that makes a national roster hard: same name, two women.
    ("M00009", "Margaret", "", "Whitfield", "Peggy", "Delta Nu", "1962", "1942", ""),
]

CHAPTERS = [
    ("C0001", "Beta Theta"), ("C0002", "Gamma Delta"), ("C0003", "Alpha Omega"),
    ("C0004", "Sigma Chi"), ("C0005", "Delta Nu"),
    ("C0006", "Sigma"),  # bare Greek letter: the trap
    ("C0007", "Rho"),
]

PAGES = [
    # cover
    "THE QUARTERLY\nVolume XLII, Number 3\nSpring 1971\n\n1",
    # maiden-name case + explicit chapter
    ("CHAPTER NEWS\n\nThe Beta Theta chapter reports that Mary Louise Ross has "
     "returned to campus as a guest lecturer. Members of the chapter gathered "
     "in the hall to hear her remarks on archival practice.\n\n2"),
    # nickname + ambiguous surname (Whitfield: Margaret AND James)
    ("ALUMNAE NOTES\n\nPeggy Whitfield writes from Cleveland with news of her "
     "work. Elsewhere, J. Whitfield has been named to the board. Nell Hargrove "
     "sends greetings from abroad.\n\n3"),
    # bare Greek letter, no chapter cue -> must be flagged
    ("A reading of Sigma was offered at the winter meeting, and Rho followed in "
     "the evening programme. The Delta Nu chapter sent its thanks.\n\n4"),
    # off-roster person -> Name Review, never exported
    ("IN MEMORIAM\n\nWe note with sorrow the passing of Harold Fenwick, a friend "
     "of the society for forty years.\n\n5"),
    # advertisement page -- thin, but still gets a row
    "PARKER & SONS\nFine Stationery\nEstablished 1889",
    # blank page -- still gets a row
    "",
    # dense page
    ("PROCEEDINGS\n\n" + ("The committee resolved that the matter be referred to "
     "the standing subcommittee for further deliberation and report. ") * 90 + "\n\n8"),
]


def main() -> None:
    AUTH.mkdir(parents=True, exist_ok=True)
    IN.mkdir(parents=True, exist_ok=True)

    # Note: NO formatted_name column -- forces the derived-name path.
    pd.DataFrame(MEMBERS, columns=[
        "member_id", "first", "middle_maiden", "last", "nickname", "chapter",
        "initiation_year", "birth_year", "death_year",
    ]).to_excel(AUTH / "members.xlsx", index=False)

    pd.DataFrame(CHAPTERS, columns=["chapter_id", "chapter_name"]).to_excel(
        AUTH / "chapters.xlsx", index=False
    )

    for issue in (1, 2):
        doc = fitz.open()
        pages = PAGES if issue == 1 else PAGES[:5]
        for text in pages:
            page = doc.new_page()
            if text:
                box = fitz.Rect(50, 50, 545, 780)
                # insert_textbox returns <0 when the text does not fit and then
                # writes NOTHING. Shrink until it fits, or the dense-page test
                # silently becomes a blank-page test.
                for size in (11, 9, 7, 6, 5, 4):
                    if page.insert_textbox(box, text, fontsize=size,
                                           fontname="helv") >= 0:
                        break
                else:
                    raise RuntimeError(f"page text would not fit: {text[:40]!r}")
        doc.save(IN / f"quarterly_1971_{issue:02d}.pdf")
        doc.close()

    print(f"members: {len(MEMBERS)}  chapters: {len(CHAPTERS)}")
    print(f"pdfs: {sorted(p.name for p in IN.glob('*.pdf'))}")


if __name__ == "__main__":
    main()
