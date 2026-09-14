# PR #10 seventh review follow-up — 2026-09-13

Baseline: `80d6067`. All five new findings were independently reproduced.

## Repairs and regression evidence

- Validate explicit region IDs before preservation, single-fragment or multi-fragment
  branches. Four unsafe IDs, including an empty explicit ID, now fail before export.
- Accept whitespace, common label punctuation and Unicode letters after complete
  leading footnote numbers, retaining the detected-footnote-region requirement.
  Six cases cover spaced, NBSP, punctuated, Unicode and joined labels and callers.
- Copy the historical rights status into rebuilt config and provenance; the test
  checks the returned config, reloaded config and provenance with a non-default value.
- Hold the project write lock over the entire structure-profile read/observe/merge/write
  operation. A concurrent probe waits for another writer and preserves that writer's
  newly published page, rules and notes when adding its own observations.
- A current failed QA report schedules revision, whose packet contains the actual QA
  errors. Missing/stale reports still schedule QA. A terminology-error integration
  test covers correction, QA rerun and progression to audit. Halfwidth punctuation is
  currently a warning, so it was not used as the failing-QA oracle.

The 14 new regression cases include 12 failures on the baseline and two existing
behaviors that already passed. Final results are recorded below and in PR Validation.

No project schema change, model installation, production PDF or translation is included.
The existing untracked `.claude/` remains untouched.

## Final local validation

`scripts/check.ps1` passed release metadata/schema validation, Ruff, Mypy (34 source
files), **757 passed / 2 skipped in 119.35 seconds**, and doctor (`ok: true`).
The two skips are the existing unavailable local WPF/Bodenschatz PDF fixtures.
The complete PR `git diff --check 38918b0` passes. CI and the subsequent Codex
review request are recorded in PR #10.
