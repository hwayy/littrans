# PR #10 fourteenth review follow-up — 2026-09-13

All four findings on `b0c8a38` were reproduced. Twelve new tests include eleven failures
on the baseline and one passing control (missing table asset references must still fail).

- Missing/tampered indexed reviews become blocking renewed-verification uncertainty.
  Rebuilding creates a new audit packet identity so a corrupt immutable review cannot
  prevent fresh review import. Tests cover accept/reject/unresolved and both damage modes,
  including fresh acceptance after damage.
- Uncertain fallback recovery dispatches before QA. Deterministic QA also checks asset
  uncertainty in the whole dependency scope, including non-translatable formula units,
  so directly running QA cannot bypass the block. QA fingerprint is v6.6, schema remains 6.
- Asset reference comparison uses the effective translated content, including table cells;
  preserved and missing placeholders are covered.
- Missing cache evidence receipts force all original PDF/SVG/PNG artifacts to regenerate;
  each file is tested with truncated pre-existing bytes and checked after retry.

No production PDFs or new model installation were used. Final checks and exact-head CI
are recorded below and in PR Validation.

Final `scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
816 passed / 2 skipped in 157.13 seconds, and doctor. The existing skipped tests need local
WPF/Bodenschatz PDFs. Full PR `git diff --check 38918b0` passed. The first full run had
one Windows Access Denied during an unrelated rebuild-directory rename (815 passed);
that test passed in isolation, then the unchanged full suite passed on rerun.
