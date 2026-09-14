# PR #10 seventeenth review follow-up — 2026-09-13

Both findings on `89b6f2a` are valid. Five baseline failures cover malformed cached JSON
and HTML footnote links inside tilde or multiline backtick fences; one cache control passes.

- The standalone worker writes JSON to a sibling temporary file, flushes/fsyncs it, then
  atomically replaces the destination. Failure/interruption removes only the temporary file.
- Unreadable cached results are cache misses. Cached and returned results must have the
  current fingerprint and complete image-key coverage; malformed worker output reports
  unavailable and can retry normally.
- HTML code tokens protect multiline backtick and tilde spans before footnote recognition.

Nine tests added; 26 focused tests passed. Atomic publication tests cover KeyboardInterrupt,
OSError, successful publication, prior-byte preservation, and temporary cleanup. Tests use
isolated worker simulation; no model installation or production PDF. Project schema stays 6.

Full `scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
844 passed / 2 skipped in 147.07 seconds, and doctor. Two existing tests need unavailable
local WPF/Bodenschatz PDFs. Full PR `git diff --check 38918b0` passed. Exact-head CI is
recorded in PR Validation after push.
