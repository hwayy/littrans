# PR #10 twenty-third review follow-up — 2026-09-13

All three findings on `b7dd1b8` were reproduced by 15 regression cases, all failing before fixes.

- Explicit/untranslated-only batches persist frozen scope. Refresh preserves selected IDs
  across sparse interiors and newly inserted units rather than adding unexpected work.
- Text/table/label companions are appended before Markdown footnote definition wrapping,
  keeping their indented ownership in both original-image modes; HTML retains one occurrence.
- Shared MathJax publication paths join the edition rollback snapshot, including absent paths.
  Individual copies use atomic writes. Six tests interrupt copying or fail later document/
  external-summary publication, verifying runtime bytes, absent-file state and prior documents.

All 15 new cases passed after fixes. Batch manifest schema adds optional `frozen_scope`;
project schema remains 6. No real model installation or production translation was performed.

Full `scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
950 passed / 2 skipped in 175.70 seconds, and doctor. The two existing skips require local
WPF/Bodenschatz PDFs. Full PR whitespace validation passed; exact-head CI is linked in PR Validation.
