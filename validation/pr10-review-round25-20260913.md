# PR #10 twenty-fifth review follow-up — 2026-09-13

Both findings on `1e1a02c` were reproduced. Nine initial cases failed, with three existing
math controls passing. Two additional controls verify missing canvases/crops still dispatch
source review after adding current-image hashing.

- Shared dollar boundaries preserve calls around currency amounts and distinguish inline
  from explicit display math. Source review and QA test a real currency-bearing caller.
- QA v6.13 includes current required-image identity and translation image receipts. QA checks
  asset-bearing dependency receipts as well as directly editable units. Tests change only the
  receipt, or republish a different canvas while asserting unchanged semantic source hashes;
  cached QA becomes stale, then passes after refreshing the viewing receipt.

All 14 new cases passed. Project schema remains 6; no real model installation or production
translation was performed.

Full `scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
987 passed / 2 skipped in 184.65 seconds, and doctor. The two existing skips require local
WPF/Bodenschatz PDFs. Full PR whitespace validation passed; exact-head CI is linked in PR Validation.
