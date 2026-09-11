# Document structure preparation validation — 2026-09-08

`./scripts/check.ps1` passed: release metadata, Ruff, Mypy (32 modules), 524 tests passed / 2 skipped, and doctor.

The full suite used the portable configuration without the optional local layout-model runtime, as on CI. Synthetic source tests explicitly review their source fixtures; this run is not a layout-model accuracy claim. Actual book extraction and visual evidence remain in the separate private translation project.

New coverage checks non-destructive probing, open-ended document rules, PDF mismatch rejection, incomplete profile review rejection, stale profile packet rejection, and parent groups interrupted by footnotes.

The prior audit-packet size assertion also failed on the pre-change source (10,341 versus 10,013 bytes). It compared schema-6 image/ownership evidence against raw legacy Markdown. The test now compares a pooled packet against separate packets with the same evidence schema and retains all three audit-lens coverage assertions.

Existing structure-module annotations/formatting were completed for strict checks. The previously committed local Codex cachebuster was restored to the shared unreleased semantic version 0.6.0; no release or plugin installation was performed.

Later note: a subsequent commit reintroduced a committed cachebuster, which `0398786` restored to 0.6.0 again. The counts above are this date's record and have since moved on; see `littrans-v06-acceptance.md` for the current re-verified numbers.
