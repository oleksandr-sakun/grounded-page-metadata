"""The validation gate.

Fuzziness lives in candidate generation. By the time a value arrives here it
must be byte-for-byte identical to an authority value or it does not ship.

Design bias: the worst-case failure of this system is UNDER-matching -- a page
routed to a human -- never a fabricated name in a workbook.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .authority import ChapterAuthority, MemberAuthority
from .llm import ModelResult
from .manifest import PageRecord

SEP = "; "


@dataclass
class PageResult:
    row: dict
    name_audit: list[dict] = field(default_factory=list)
    name_review: list[dict] = field(default_factory=list)
    chapter_audit: list[dict] = field(default_factory=list)
    chapter_review: list[dict] = field(default_factory=list)
    qa_flags: list[dict] = field(default_factory=list)


def validate_page(
    page: PageRecord,
    result: ModelResult,
    names,
    chapters,
    spans: set[str],
    members: MemberAuthority,
    chapter_auth: ChapterAuthority,
    pdf_name: str,
    taxonomy: list[str] | None,
) -> PageResult:
    flags: list[dict] = []

    def flag(code: str, detail: str = "") -> None:
        flags.append(
            {"Asset Page #": page.asset_page, "Source PDF": pdf_name,
             "Flag": code, "Detail": detail}
        )

    # A row exists no matter what happened upstream.
    row = {
        "Order": page.asset_page,
        "Asset Page #": page.asset_page,
        "Asset Filename Placeholder": f"{pdf_name}_p{page.asset_page:04d}",
        "Printed Page #": "",
        "Page Title": "",
        "Names Referenced": "",
        "Name Review": "",
        "Greek Chapter Referenced": "",
        "Chapter Review": "",
        "Subjects": "",
        "Suggested Subjects": "",
        "Notes for Review": "",
    }

    if page.is_low_text:
        flag("LOW_OCR_TEXT", f"{page.char_count} chars")
    if page.is_dense:
        flag("DENSE_PAGE", f"{page.char_count} chars -- manual review advised")

    if result.interpretation is None:
        row["Notes for Review"] = "Model call failed; page requires manual review."
        flag("MODEL_ERROR", result.error)
        return PageResult(row=row, qa_flags=flags)

    interp = result.interpretation
    by_id = {c.member_id: c for c in names}
    chap_by_id = {c.chapter_id: c for c in chapters}

    row["Page Title"] = interp.page_title.strip()
    row["Printed Page #"] = interp.printed_page_number.strip()
    if not row["Printed Page #"] and not page.is_low_text:
        flag("MISSING_PRINTED_PAGE", "page has text but no printed number found")

    # ---- names -------------------------------------------------------------
    final_names, review_names = [], []
    audit, review_detail = [], []

    for mid in interp.member_ids:
        cand = by_id.get(mid)
        if cand is None:
            # Should be unreachable: the enum forbids it. Belt and braces.
            flag("INVALID_MEMBER_ID", mid)
            continue
        authoritative = members.members[mid].formatted_name
        if authoritative not in members.valid_names:
            flag("INVALID_NAME", authoritative)
            review_names.append(cand.observed_text)
            continue
        final_names.append(authoritative)
        audit.append({
            "Asset Page #": page.asset_page, "Observed Text": cand.observed_text,
            "Match Path": cand.match_path, "Member ID": mid,
            "Authority Name": authoritative, "Status": "accepted",
        })

    for mid in interp.ambiguous_member_ids:
        cand = by_id.get(mid)
        if not cand:
            continue
        review_names.append(cand.observed_text)
        review_detail.append({
            "Asset Page #": page.asset_page, "Observed Text": cand.observed_text,
            "Reason": "AMBIGUOUS_CANDIDATES",
            "Candidates": SEP.join(
                sorted({c.formatted_name for c in names
                        if c.observed_text == cand.observed_text})),
        })
        audit.append({
            "Asset Page #": page.asset_page, "Observed Text": cand.observed_text,
            "Match Path": cand.match_path, "Member ID": mid,
            "Authority Name": members.members[mid].formatted_name,
            "Status": "deferred_to_review",
        })

    # Candidates the model declined to select.
    selected = set(interp.member_ids) | set(interp.ambiguous_member_ids)
    chosen_spans = {c.observed_text for c in names if c.member_id in selected}
    for obs in sorted({c.observed_text for c in names} - chosen_spans):
        review_names.append(obs)
        review_detail.append({
            "Asset Page #": page.asset_page, "Observed Text": obs,
            "Reason": "CANDIDATE_NOT_SELECTED", "Candidates": "",
        })

    # Person-like text with NO authority candidate at all. This is the roster
    # gap case. An earlier version of this code let it pass in silence -- the
    # page simply had no names -- which is the most dangerous kind of clean
    # output: wrong, and confident about it.
    for obs in sorted(spans - {c.observed_text for c in names}):
        review_names.append(obs)
        review_detail.append({
            "Asset Page #": page.asset_page, "Observed Text": obs,
            "Reason": "NO_AUTHORITY_MATCH",
            "Candidates": "(person named on page is absent from the roster)",
        })

    if review_names:
        flag("NAME_REVIEW", SEP.join(sorted(set(review_names))))

    # ---- chapters ----------------------------------------------------------
    # Single-letter chapter names are flagged on MENTION, not on selection.
    # Leaving this to the model meant a model that quietly dropped "Sigma"
    # produced a page with no chapter and no flag: invisible data loss.
    final_chapters, review_chapters = [], []
    for c in chapters:
        if c.single_letter and not c.has_context_cue:
            review_chapters.append(c.chapter_name)
            flag("SINGLE_LETTER_CHAPTER", c.chapter_name)

    for cid in interp.chapter_ids:
        cand = chap_by_id.get(cid)
        if cand is None:
            flag("INVALID_CHAPTER_ID", cid)
            continue
        if cand.chapter_name not in chapter_auth.valid_chapters:
            flag("INVALID_CHAPTER", cand.chapter_name)
            continue
        if cand.chapter_name in review_chapters:
            continue  # already deferred to a human
        final_chapters.append(cand.chapter_name)

    chapter_audit = [
        {"Asset Page #": page.asset_page, "Observed Text": c.observed_text,
         "Chapter ID": c.chapter_id, "Authority Chapter": c.chapter_name,
         "Single Letter": c.single_letter, "Context Cue": c.has_context_cue,
         "Status": ("accepted" if c.chapter_name in final_chapters
                    else "flagged" if c.chapter_name in review_chapters
                    else "not_selected")}
        for c in chapters
    ]
    chapter_review = [
        {"Asset Page #": page.asset_page, "Observed Text": name,
         "Reason": "SINGLE_LETTER_NO_CONTEXT"}
        for name in review_chapters
    ]
    if review_chapters:
        flag("CHAPTER_REVIEW", SEP.join(sorted(set(review_chapters))))

    # ---- subjects ----------------------------------------------------------
    subjects = interp.subjects
    if taxonomy:
        off = [s for s in subjects if s not in taxonomy]
        subjects = [s for s in subjects if s in taxonomy]
        if off:
            flag("OFF_TAXONOMY_SUBJECT", SEP.join(off))

    row["Names Referenced"] = SEP.join(sorted(set(final_names)))
    row["Name Review"] = SEP.join(sorted(set(review_names)))
    row["Greek Chapter Referenced"] = SEP.join(sorted(set(final_chapters)))
    row["Chapter Review"] = SEP.join(sorted(set(review_chapters)))
    row["Subjects"] = SEP.join(subjects)
    row["Suggested Subjects"] = SEP.join(interp.suggested_subjects)
    row["Notes for Review"] = interp.notes_for_review.strip()

    # The final gate. Nothing leaves without passing it.
    assert all(n in members.valid_names for n in final_names)
    assert all(c in chapter_auth.valid_chapters for c in final_chapters)

    return PageResult(row=row, name_audit=audit, name_review=review_detail,
                      chapter_audit=chapter_audit, chapter_review=chapter_review,
                      qa_flags=flags)
