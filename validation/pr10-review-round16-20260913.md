# PR #10 sixteenth review follow-up — 2026-09-13

All three findings on `e885c6c` are valid. Four new regression cases failed on the baseline.

- Explicit empty target text no longer falls back to source text. Structured target tables
  take the table serialization path, and the obsolete Markdown append is removed.
- The obsolete HTML target-table append is removed; source and translated tables each
  render exactly once. Full render_project outputs are checked in both original-image
  settings, along with source-review HTML and actual asset-placeholder expansion.
- The shared call tokenizer protects tilde fences, matching Markdown's existing handling.
  QA cases cover three-tilde and longer language-tagged fences; QA fingerprint is v6.8.

Eighteen focused tests passed. No project schema change, production PDF, or model install.
Full checks and exact-head CI are recorded below/in PR Validation.

Full `scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
835 passed / 2 skipped in 131.41 seconds, and doctor. Two existing tests need unavailable
local WPF/Bodenschatz PDFs. Full PR `git diff --check 38918b0` passed.
