# PR #10 eleventh review follow-up — 2026-09-13

Baseline: `9dfb86e`. All three findings were independently reproduced.

- Batch output-schema.json is generated from the actual TranslationRecord model,
  including image evidence mappings and full asset companion definitions. Batch refresh
  replaces the old emitted schema, preventing existing batches from retaining the defect.
- Workflow packet identity version 4 and manifests bind the selected host, model and
  reasoning effort. Same-policy replay is stable; changing host, model or effort creates
  a distinct translate/revise packet. Optional fields keep old manifests readable.
- One shared footnote graph validator checks both current pages and final retained-plus-
  replacement units before publication. Invalid imports roll back authoritative evidence
  and allow fresh packet creation; references to footnotes created later in the same
  transaction remain valid.

The initial six regression cases failed before repair. Eight final cases pass, including
emitted-schema refresh, both translate/revise policy binding, typo/non-footnote/duplicate
references, full state rollback and valid forward references across replaced pages.
Project schema remains 6. No production PDF, model installation or new browser behavior
is involved. Final full checks and CI are recorded below and in PR #10.

`scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
**787 passed / 2 skipped in 134.23 seconds**, and doctor. The two skips are the
unavailable local WPF/Bodenschatz PDF fixtures. Whole-PR whitespace checks pass.
