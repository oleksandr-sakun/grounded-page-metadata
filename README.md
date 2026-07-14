# grounded-page-metadata

A deterministic batch metadata processor for OCR'd historical publications.
Code owns the page manifest, candidate retrieval, validation and export.
**The model advises. It never owns a row, a name, or a chapter.**

```bash
pip install -r requirements.txt
python tools/make_synthetic_data.py      # fake roster + fake OCR'd PDFs
PYTHONPATH=src python -m gpm.cli --dry-run
PYTHONPATH=src python -m pytest tests/ -q
```

No API key required. `--dry-run` swaps in an offline stub model, so the full
pipeline — manifest, retrieval, validation, seven-sheet workbook, QA flags —
runs end to end in about a second.

---

## The problem this is built against

Archival metadata is a domain where a plausible wrong answer is far worse than
no answer. A fabricated member name does not fail loudly; it enters a permanent
record and is trusted forever. The naive LLM approach — hand the model the page
and the roster, ask for the names — fails in four ways that all look like
success:

| Failure | Why the obvious design permits it |
|---|---|
| Invented names | The model emits strings. Strings can be anything. |
| Dropped pages | The model decides what counts as a page worth a row. |
| Silent roster gaps | A person absent from the roster produces an empty, confident row. |
| Guessed disambiguation | Two members share a name; the model picks one and never says so. |

## The design

```
PDF ──► page manifest (code)          ← row count fixed here, before any API call
         │
         ├─► OCR text per page (code)
         ├─► name candidates (code)   ← multi-key index over the authority file
         └─► chapter candidates (code)
                  │
                  ▼
            model call  ← candidate IDs injected as a JSON-Schema *enum*
                  │        model returns IDs, never names
                  ▼
            validation gate (code)    ← exact match against authority, or it does not ship
                  │
                  ▼
     Metadata · Name_Match_Audit · Name_Review_Detail · Chapter_Audit
     Chapter_Review_Detail · QA_Flags · Run_Notes   + CSV + batch_summary
```

**The load-bearing detail** is in `llm.build_schema()`. The enum of permitted
member IDs is rebuilt per page from that page's candidates. On a page where
code found no candidate, the schema for `member_ids` admits nothing but the
empty array. Fabrication is not discouraged by the prompt — it is unrepresentable
in the output type.

Everything downstream assumes the model is adversarial. A hallucinated ID, a
schema violation, a dead API, a refusal: each produces a **flagged row**, never
a missing one and never a wrong one.

## What the model actually decides

Page title, printed page number, which of the code-supplied candidates the page
really refers to, subjects, and review notes. That is all. It does not control
row count, final values, or validation.

## Name matching

The publication prints a woman's college-era or maiden name; the roster lists
her married name. So each member is indexed under every plausible printed form —
the cross product of `{first, nickname, diminutive, initial}` × `{last, maiden,
middle/maiden tokens, maiden+married}` — while the **exported** value stays
pinned to one canonical string, derived as `Last, Suffix, Prefix First Middle Maiden`
when the roster has no formatted-name column.

Worked example, from the test suite: the page prints *Mary Louise Ross*. The
roster holds `first=Mary, middle_maiden=Louise Ross, last=Arronte`. The key
`(mary, ross)` resolves her, and the exported value is **`Arronte, Mary Louise Ross`** —
the authority's string, not the page's.

When two members collide (the corpus contains two Margaret "Peggy" Whitfields),
the row does not get a guess. It gets `AMBIGUOUS_CANDIDATES` and both names, for
a human.

## Why the roster is never sent to the model

Three reasons, in order of how much they cost you:

1. **Accuracy.** A long in-context roster is what tempts a model to blend two
   similar members into a plausible third. A short enum makes that impossible.
2. **Cost at scale.** Sending the roster per page makes cost scale as
   `pages × roster size`. Code-side retrieval keeps cost per page flat no matter
   how large the authority file grows.
3. **Auditability.** "The model found it in the list" is not a rule path.
   `Name_Match_Audit` records observed text → match path → member ID → status
   for every decision.

## Three bugs this repo found in itself

Kept in the history on purpose — they are the actual content of the domain.

- **A bare "Sigma" was exported as a chapter.** The context-cue window accepted
  *"at the winter meeting"* as chapter context. Single-letter Greek names now
  require an explicit chapter word within 40 characters, or they are flagged.
- **An off-roster person vanished.** *Harold Fenwick* generated no candidate,
  so nothing surfaced him and the page came back cleanly empty. Person-like
  spans are now extracted independently of the roster; a span with no candidate
  becomes `NO_AUTHORITY_MATCH` in Name Review. A roster gap must be visible.
- **A single-letter chapter dropped by the model produced no flag.** Flagging
  now happens on *mention*, not on model selection — otherwise a model that
  quietly ignores "Rho" causes invisible data loss.

## Accuracy report

The processor is scored against a human-approved workbook — the client's own
sign-off, not a self-graded metric.

```bash
python tools/make_gold_workbook.py
PYTHONPATH=src python -m gpm.score
```

```
Source                Pages  Names  Correct  Fabrications  Deferred  Silent misses  Precision  Recall  Coverage  Verdict
quarterly_1971_01         8      4        3             0         1              0        1.0    0.75       1.0     PASS
```

**Recall is 0.75 and that is the correct answer.** The missing name is the
ambiguous Peggy Whitfield — two members share it, so the processor refused to
guess and sent her to a human instead. An F1 score would punish that exactly as
hard as losing her in silence. These are not the same event, so the report
separates them:

| Verdict | Meaning | Cost |
|---|---|---|
| `FABRICATION` | exported a name no human approved | **critical** — fails the run, exit 1 |
| `SILENT_MISS` | approved name neither exported nor flagged | high — the page looks clean and nobody returns to it |
| `DEFERRED_TO_REVIEW` | not exported, but routed to Name Review | fine — the system worked |

Hence **coverage** = (correct + deferred) / approved. Its complement is the only
number that should frighten you: data lost without a trace. A fabrication exits
non-zero, so this drops straight into CI.

## Tests

Each test is a design claim, made executable.

```
test_row_count_equals_page_count              one row per page, always
test_blank_and_ad_pages_still_get_rows        covers/blanks/ads are not content decisions
test_model_failure_does_not_delete_a_row      a dead API is a flagged row, not a gap
test_every_exported_name_exists_in_authority  no name escapes the whitelist
test_schema_forbids_names_when_no_candidates  fabrication is unrepresentable
test_maiden_name_reference_resolves...        Mary Louise Ross → Arronte, Mary Louise Ross
test_ambiguous_name_is_deferred_not_guessed   two Peggy Whitfields → review, not a coin flip
test_roster_gap_is_surfaced_not_swallowed     Harold Fenwick reaches a human
test_chapter_never_inferred_from_member_record chapters come from the page, never the roster
test_bare_greek_letter_flagged_while_attested_chapter_exports
test_scoring_flags_a_fabrication_as_critical  a poisoned workbook must FAIL
test_deferred_miss_scores_better_than_silent_miss
```

## Running against real data

```bash
cp .env.example .env          # add OPENAI_API_KEY
PYTHONPATH=src python -m gpm.cli \
  --pdfs data/input --members data/authority/members.xlsx \
  --chapters data/authority/chapters.xlsx --model gpt-4o-mini
```

Roster columns are matched case- and space-insensitively: `member_id`, `first`,
`middle_maiden`, `last`, `nickname`, `prefix`, `suffix`, `chapter`,
`initiation_year`, `birth_year`, `death_year`, and optionally `formatted_name`
(if absent, it is derived). `--nicknames` and `--taxonomy` take JSON files.

Every run writes `Run_Notes` and `*_processing_log.json` recording the model,
prompt hash, and a content hash of each authority file — so any output can be
re-validated against the exact inputs that produced it.

## Known limits (prototype scope)

- Person-span detection is regex-based. Production would use NER; the regex is
  tuned for recall, and its false positives land in Name Review, where they are
  cheap.
- No vision fallback for pages whose OCR layer is empty. Those are flagged
  `LOW_OCR_TEXT` and routed to a human.
- Single-threaded, one API call per page. Batching is a Phase-2 concern.
- The live `OpenAIModel` path is implemented but has not yet been exercised
  against a funded API key; every run so far has used `--dry-run`. Notably, the
  three failed live attempts became the best possible test of the core claim:
  the API was dead for all 13 pages and the processor still produced 13 rows,
  zero fabrications, and a `MODEL_ERROR` flag on every page.
