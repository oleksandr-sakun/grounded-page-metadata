"""Each test here is a claim from the proposal, made executable."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import fitz
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gpm.authority import (  # noqa: E402
    build_chapter_authority, build_member_authority, derive_formatted_name,
)
from gpm.llm import ModelResult, StubModel, build_schema  # noqa: E402
from gpm.pipeline import Run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def corpus():
    subprocess.run(
        [sys.executable, "tools/make_synthetic_data.py"], cwd=ROOT, check=True
    )
    return ROOT


@pytest.fixture(scope="session")
def run(corpus, tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    r = Run(
        members=build_member_authority(corpus / "data/authority/members.xlsx"),
        chapters=build_chapter_authority(corpus / "data/authority/chapters.xlsx"),
        model=StubModel(),
        prompt=(corpus / "prompts/page_metadata.md").read_text(),
        taxonomy=None,
        out_dir=out,
    )
    r.process_folder(corpus / "data/input")
    return r, out


def sheet(out: Path, stem: str, name: str) -> pd.DataFrame:
    return pd.read_excel(out / f"{stem}_metadata_draft.xlsx", sheet_name=name)


# --- Claim 1: exactly one row per page, always ------------------------------

def test_row_count_equals_page_count(run, corpus):
    _, out = run
    for pdf in sorted((corpus / "data/input").glob("*.pdf")):
        with fitz.open(pdf) as doc:
            pages = doc.page_count
        meta = sheet(out, pdf.stem, "Metadata")
        assert len(meta) == pages
        assert list(meta["Asset Page #"]) == list(range(1, pages + 1))


def test_blank_and_ad_pages_still_get_rows(run, corpus):
    _, out = run
    meta = sheet(out, "quarterly_1971_01", "Metadata")
    assert len(meta) == 8              # incl. blank p7 and advertisement p6
    assert 7 in set(meta["Asset Page #"])


def test_model_failure_does_not_delete_a_row(run, corpus, tmp_path):
    """A dead API is a flagged row, not a missing one."""
    class DeadModel:
        name = "dead"
        def interpret(self, *a, **k):
            return ModelResult(interpretation=None, error="ConnectionError: boom")

    r, _ = run
    r2 = Run(members=r.members, chapters=r.chapters, model=DeadModel(),
             prompt=r.prompt, taxonomy=None, out_dir=tmp_path)
    r2.process_pdf(corpus / "data/input/quarterly_1971_01.pdf")
    meta = sheet(tmp_path, "quarterly_1971_01", "Metadata")
    qa = sheet(tmp_path, "quarterly_1971_01", "QA_Flags")
    assert len(meta) == 8
    assert (qa["Flag"] == "MODEL_ERROR").sum() == 8
    assert meta["Names Referenced"].fillna("").eq("").all()


# --- Claim 2: no invented names, structurally ------------------------------

def test_every_exported_name_exists_in_authority(run):
    r, out = run
    valid = r.members.valid_names
    for f in out.glob("*_metadata_draft.xlsx"):
        meta = pd.read_excel(f, sheet_name="Metadata").fillna("")
        for cell in meta["Names Referenced"]:
            for name in [n for n in str(cell).split("; ") if n]:
                assert name in valid, f"fabricated name escaped: {name!r}"


def test_schema_forbids_names_when_no_candidates_exist():
    """The strongest guarantee: on a page with no candidate, the schema admits
    no member id at all. Fabrication is not penalised, it is impossible."""
    s = build_schema([], [], None)
    assert s["properties"]["member_ids"]["maxItems"] == 0


def test_off_roster_person_goes_to_review_not_to_names(run):
    _, out = run
    meta = sheet(out, "quarterly_1971_01", "Metadata").fillna("")
    p5 = meta[meta["Asset Page #"] == 5].iloc[0]
    assert "Fenwick" not in p5["Names Referenced"]
    assert "Fenwick" in p5["Name Review"]


# --- Claim 3: maiden / nickname resolution ---------------------------------

def test_derived_formatted_name_rule():
    assert derive_formatted_name(
        {"first": "Mary", "middle_maiden": "Louise Ross", "last": "Arronte"}
    ) == "Arronte, Mary Louise Ross"


def test_maiden_name_reference_resolves_to_married_authority_name(run):
    _, out = run
    meta = sheet(out, "quarterly_1971_01", "Metadata").fillna("")
    p2 = meta[meta["Asset Page #"] == 2].iloc[0]
    # page prints "Mary Louise Ross"; authority says Arronte
    assert p2["Names Referenced"] == "Arronte, Mary Louise Ross"
    audit = sheet(out, "quarterly_1971_01", "Name_Match_Audit")
    assert "Maiden" in audit[audit["Member ID"] == "M00001"]["Match Path"].iloc[0]


def test_nickname_resolves(run):
    _, out = run
    audit = sheet(out, "quarterly_1971_01", "Name_Match_Audit").fillna("")
    nell = audit[audit["Observed Text"] == "Nell Hargrove"]
    assert not nell.empty
    assert nell["Authority Name"].iloc[0] == "Hargrove, Eleanor"
    assert nell["Match Path"].iloc[0].startswith("Nickname")


def test_initial_plus_surname_resolves(run):
    _, out = run
    audit = sheet(out, "quarterly_1971_01", "Name_Match_Audit").fillna("")
    j = audit[audit["Observed Text"] == "J. Whitfield"]
    assert not j.empty
    assert j["Authority Name"].iloc[0] == "Whitfield, James"


# --- Claim 4: ambiguity is deferred, never guessed --------------------------

def test_ambiguous_name_is_deferred_not_guessed(run):
    """Two Margaret "Peggy" Whitfields exist. A coin flip is not an answer."""
    _, out = run
    meta = sheet(out, "quarterly_1971_01", "Metadata").fillna("")
    p3 = meta[meta["Asset Page #"] == 3].iloc[0]
    assert "Peggy Whitfield" in p3["Name Review"]
    assert "Margaret" not in p3["Names Referenced"]

    detail = sheet(out, "quarterly_1971_01", "Name_Review_Detail").fillna("")
    row = detail[detail["Observed Text"] == "Peggy Whitfield"].iloc[0]
    assert row["Reason"] == "AMBIGUOUS_CANDIDATES"
    assert row["Candidates"].count(";") == 1   # both candidates shown to the human


def test_roster_gap_is_surfaced_not_swallowed(run):
    """A person on the page who is absent from the roster must not produce a
    clean, silent, empty row."""
    _, out = run
    detail = sheet(out, "quarterly_1971_01", "Name_Review_Detail").fillna("")
    row = detail[detail["Observed Text"] == "Harold Fenwick"].iloc[0]
    assert row["Reason"] == "NO_AUTHORITY_MATCH"


def test_dense_page_is_flagged(run):
    _, out = run
    qa = sheet(out, "quarterly_1971_01", "QA_Flags")
    assert (qa["Flag"] == "DENSE_PAGE").any()


# --- Claim 5: chapters only on explicit mention -----------------------------

def test_bare_greek_letter_flagged_while_attested_chapter_exports(run):
    """Page 4 prints "Sigma" and "Rho" bare, and "Delta Nu chapter" properly.
    The processor must tell these apart -- flag the first two, export the third."""
    _, out = run
    meta = sheet(out, "quarterly_1971_01", "Metadata").fillna("")
    p4 = meta[meta["Asset Page #"] == 4].iloc[0]
    assert p4["Greek Chapter Referenced"] == "Delta Nu"
    assert "Sigma" in p4["Chapter Review"]
    assert "Rho" in p4["Chapter Review"]
    assert "Sigma" not in p4["Greek Chapter Referenced"]


def test_chapter_never_inferred_from_member_record(run):
    """Page 3 names Beta Theta members but never prints a chapter. The chapter
    column must stay empty -- the most tempting shortcut in this domain."""
    _, out = run
    meta = sheet(out, "quarterly_1971_01", "Metadata").fillna("")
    p3 = meta[meta["Asset Page #"] == 3].iloc[0]
    assert p3["Greek Chapter Referenced"] == ""


def test_explicit_chapter_is_exported(run):
    _, out = run
    meta = sheet(out, "quarterly_1971_01", "Metadata").fillna("")
    p2 = meta[meta["Asset Page #"] == 2].iloc[0]
    assert p2["Greek Chapter Referenced"] == "Beta Theta"


# --- Claim 6: scoring separates a deferral from a loss ----------------------

def test_scoring_flags_a_fabrication_as_critical(run, corpus, tmp_path):
    """Poison a produced workbook with a name no human approved. The scorer
    must call it a FABRICATION and fail the run -- otherwise the accuracy
    report is decoration."""
    import subprocess
    from gpm.score import score_pdf

    subprocess.run([sys.executable, "tools/make_gold_workbook.py"], cwd=ROOT,
                   check=True, env={**os.environ, "PYTHONPATH": "src:tools"})
    _, out = run

    poisoned = tmp_path / "quarterly_1971_01_metadata_draft.xlsx"
    src = out / "quarterly_1971_01_metadata_draft.xlsx"
    meta = pd.read_excel(src, sheet_name="Metadata").fillna("")
    meta.loc[meta["Asset Page #"] == 6, "Names Referenced"] = "Nobody, Ida Invented"
    with pd.ExcelWriter(poisoned, engine="openpyxl") as xw:
        meta.to_excel(xw, sheet_name="Metadata", index=False)

    row, diffs = score_pdf(poisoned, ROOT / "data/gold/quarterly_1971_01_approved.xlsx")
    assert row["Name fabrications"] == 1
    assert row["Verdict"] == "FAIL"
    assert any(d["Verdict"] == "FABRICATION" and d["Severity"] == "critical"
               for d in diffs)


def test_deferred_miss_scores_better_than_silent_miss(run, corpus):
    """The whole point of the coverage metric: the ambiguous Peggy Whitfield is
    absent from Names Referenced, yet nothing was lost -- she reached a human."""
    from gpm.score import score_pdf
    _, out = run
    row, diffs = score_pdf(
        out / "quarterly_1971_01_metadata_draft.xlsx",
        ROOT / "data/gold/quarterly_1971_01_approved.xlsx",
    )
    assert row["Name recall"] < 1.0        # she is not in the final column
    assert row["Name coverage"] == 1.0     # ...but she is not lost either
    assert row["Name silent misses"] == 0
    assert any(d["Verdict"] == "DEFERRED_TO_REVIEW" for d in diffs)
