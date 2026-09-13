# PR #10 twenty-fourth review follow-up — 2026-09-13

All three findings on `9c02644` were reproduced. Twenty-one initial cases expose code-fence
and whitespace failures; after isolating host runtime overrides, two model cases also fail
under the old two-file download condition (re-executed in memory without changing source).

- QA v6.12, Markdown and HTML share line-valid fenced-code recognition. Sixteen cases
  cover backtick/tilde runs, mid-line/trailing-text false closers, longer valid closing fences,
  indentation and unterminated blocks. Two QA cases reject a literal call replacing a real call.
- Managed installation redownloads when the previous READY marker is absent, even with both
  primary model files present. Copy and smoke failures recover on ordinary non-forced retry.
- Bold run-in detection checks the filtered previous glyph list before indexing; three
  whitespace cases retain empty-line and prior-bold controls.

All 23 new cases passed. Project schema remains 6; no real model download or installation
was performed. Runtime failure/retry paths use isolated mocks.

The first full run passed 970 tests and exposed three old expectations: retries must now
redownload, and two tilde-fence fixtures needed a line break before their opening fence.
These assertions were updated to the intended behavior without removing coverage.

Final `scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
973 passed / 2 skipped in 204.95 seconds, and doctor. The two existing skips require local
WPF/Bodenschatz PDFs. Full PR whitespace validation passed; exact-head CI is linked in PR Validation.
