# PR #10 twelfth review follow-up — 2026-09-13

Baseline: `efbc18b`. All three findings were reproduced.

- Footnote graph validation requires the set of explicit call numbers to match unique
  referenced definition numbers. Code/math/escaped literals are excluded; repeated calls
  to one definition are valid. Previous fixtures with abstract relationships but no
  actual calls/definitions were completed to satisfy the source contract.
- Uncertain fallback assets remain pending transcription recovery. Workflow packets
  prioritize recovery over fresh enhancement and include immutable prior candidate/review
  feedback. If current QA fails only for asset semantic uncertainty, next dispatches
  transcription instead of prose revision. A clean revised candidate proceeds to audit
  and requires QA to rerun. Ordinary fallback without uncertainty remains complete.
- Source authority transactions catch BaseException, restore snapshots and re-raise;
  Ctrl-C during prepare and import-review no longer releases a partially published state.

Eleven new cases cover wrong/missing/duplicate footnote numbers, repeated legitimate calls,
literal syntax, both interruption paths, and reject/unresolved asset recovery through
revision-context submission. The initial five graph/recovery cases failed on the baseline;
the corrected interruption test was also run with the old Exception handler and confirmed
that authoritative files were left changed. With the fix, both interruption paths restore
their prior bytes and release the write lock.

No project schema change, model installation or production PDF is included. Final full
checks and CI are recorded below and in PR #10.

Final `scripts/check.ps1`: release metadata/schema, Ruff, Mypy (34 source files),
798 passed / 2 skipped in 128.74 seconds, and doctor passed. The two existing skips
require unavailable local WPF/Bodenschatz PDFs. The full PR `git diff --check 38918b0`
passed. Final CI status is recorded in PR Validation after pushing this commit.
