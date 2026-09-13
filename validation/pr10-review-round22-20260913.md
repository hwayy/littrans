# PR #10 twenty-second review follow-up — 2026-09-13

All five findings on `7c9176a` were reproduced (four inline discussions plus the render-cover
finding in the review body). Fifteen initial cases failed before fixes, including seven
continuation cases; two continuation controls already passed. Three extra cases cover
missing editable ownership and a QA-current but unaudited dependency manifest.

- Workflow rechecks source authority before accepting cached QA. Missing/corrupt receipts
  in the selected or dependent page dispatch `source-review`; unrelated pages stay complete.
  Source-review packet creation includes the source-evidence closure, with no optional asset work.
- Pydantic's minimum is 2.12, which introduced `Field(exclude_if=...)`:
  https://pydantic.dev/articles/pydantic-v2-12-release . An isolated Pydantic 2.12.0 installation
  passed schema, asset representation and the initial round-22 tests, without replacing the
  main virtual environment.
- Markdown and HTML defer companions until paragraph/note/list continuation chains finish.
  First/last/both fragment placements preserve order, multiplicity and closing-tag shape.
- Blocking read-only review issues route to editable owners; packets carry issues from their
  original batches, retaining originating batch IDs for resolution. Missing ownership errors
  explicitly rather than dispatching an impossible task.
- Formal dependency covers filter current QA/audit/external gates before cover ranking,
  so a newer stale retained manifest does not displace valid evidence.

All 20 new cases passed. Project schema remains 6; no real model installation was performed.

The first full run passed 934 tests and exposed a source-only blocker case with no editable
translation owner. It now dispatches source review and includes originating workflow issues
in the returned source-review materials; the existing test retains its non-completion check.

Final `scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
935 passed / 2 skipped in 181.57 seconds, and doctor. The two existing skips require local
WPF/Bodenschatz PDFs. Full PR `git diff --check 38918b0` passed; exact-head CI is linked
in PR Validation.
