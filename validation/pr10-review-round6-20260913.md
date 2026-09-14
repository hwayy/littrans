# PR #10 sixth review follow-up — 2026-09-13

Baseline: `dbb7996`. Its three findings were confirmed fixed and resolved.
Of the next four findings, three were reproduced and repaired; the dependency-page
verification finding was independently disproved against the existing implementation.

## Repairs

- Override unit IDs must match `[A-Za-z0-9][A-Za-z0-9._-]*` before page changes. Both
  raw Markdown anchor sinks escape legacy IDs, protecting existing workspaces too.
- The Markdown footnote tokenizer protects `\(...\)` and `\[...\]` as well as
  the existing dollar-delimited math and code spans; adjacent real calls still rewrite.
- Structure planning uses alphabetic glyph sizes when available, otherwise all usable
  finite positive sizes, or the default for empty/unusable glyph sets. Numeric and
  symbol-only PDFs reach source preparation and remain subject to normal visual review.

## Finding not reproduced: source-render dependency-page verification

`source_render` calls `verification.verify_extraction`, which delegates schema-6 work
to `fidelity.verify_fidelity`. That function expands requested pages with
`evidence.page_evidence_units`, including footnote references, before checking receipts.
Three new integration cases first establish a valid cross-page review, then remove,
stale or corrupt the dependency page receipt. All three returned `source_verified=false`
on the baseline before implementation changes. No functional source-render change was
needed for this finding. The tests retain the existing asset references so an unrelated
coverage defect cannot explain the failed verification.

## Validation

Nine unsafe-ID/math/non-alphabetic regression cases failed before repair. The final
new test file has 17 cases including legacy-anchor escaping, empty-page behavior,
dollar math, the three baseline dependency cases and actual numeric/symbol preparation.

`scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
**743 passed / 2 skipped in 107.41 seconds**, and doctor. Windows/Python 3.13.14
with `PIP_NO_CACHE_DIR=1` for existing cache permissions.

The two existing local WPF/Bodenschatz PDF tests skip for unavailable fixtures.
Full PR `git diff --check 38918b0` passes. Project schema remains 6; `.claude/` and
archived patch bytes are untouched. No production PDFs or review evidence are committed.
No model install/inference or new browser visual inspection was needed. Local logs
remain in ignored `tmp/pr10-round6-*.log`. CI and the next review are recorded in PR #10.
