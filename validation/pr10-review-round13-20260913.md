# PR #10 thirteenth review follow-up — 2026-09-13

The finding on `2eb1c2c` is valid: submitting a recovery candidate cleared the prior
reviewer's semantic uncertainty before independent review. Six updated regression paths
failed on that baseline. Candidates now inherit reviewer uncertainty independently of
their own submission field. A valid subsequent review supersedes it only after artifact
verification; workflow schedules the pending asset audit before QA.

Eight recovery cases cover reject/unresolved decisions through workflow packets, direct
packets, repeated revision, and legacy candidates lacking the new internal field. Each
checks that QA remains blocked before audit, passes after acceptance, and blocks again
if the accepted render artifact is damaged. Two old assertions were corrected and six
cases added. Project schema remains 6; no model installation or production PDF used.

Full `scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
804 passed / 2 skipped in 127.71 seconds, and doctor. The two existing skips require
unavailable local WPF/Bodenschatz PDFs. Full PR `git diff --check 38918b0` passed.
The exact pushed commit's CI result is recorded in PR Validation.
