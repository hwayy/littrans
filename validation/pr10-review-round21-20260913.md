# PR #10 twenty-first review follow-up — 2026-09-13

The companion-footnote finding on `2ec2977` was reproduced in all four rendered fields:
target text, table cells, source labels and translated labels. Twelve regression cases
failed before the fix; eight escaped/code-literal controls already passed.

QA v6.11 rejects live footnote calls in companions through the shared companion validator,
keeping calls in the main translation. The existing footnote tokenizer preserves escaped
and code-literal notation. Render-output assertions verify which examples create HTML
links; four integration cases verify rejection after a previously cached QA pass.

All 20 new cases passed after the fix. Project schema remains 6; existing QA must rerun.

Full `scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
915 passed / 2 skipped in 167.25 seconds, and doctor. The two existing skips require local
WPF/Bodenschatz PDFs; no real model installation or production translation was performed.
Full PR whitespace validation passed; exact-head CI is linked in PR Validation.
