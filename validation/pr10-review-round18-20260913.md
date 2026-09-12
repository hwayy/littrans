# PR #10 eighteenth review follow-up — 2026-09-13

Both findings on `b18922b` were reproduced. Nine new regression cases pass after repair.

- Source-review layout scans skip unreadable entries and require a valid matching payload;
  missing valid evidence retains the explicit unavailable fallback. Six cases cover three
  malformed JSON forms, with and without a later matching valid cache.
- Coalesced tables aggregate companions from every translation fragment, preserving each
  original source unit's rendering context. Three full-reading-output tests cover later
  fragment prose, table, and label companions appearing exactly once in Markdown and HTML.

No schema change, model installation, or production PDF. Full checks and exact-head CI
are recorded below/in PR Validation.

Full `scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
853 passed / 2 skipped in 157.67 seconds, and doctor. Two existing tests need unavailable
local WPF/Bodenschatz PDFs. Full PR `git diff --check 38918b0` passed.
