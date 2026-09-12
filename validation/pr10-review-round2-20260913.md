# PR #10 second review follow-up — 2026-09-13

Baseline: `8fefb66`. The first eight findings were checked against the implementation
and their regression coverage (59 targeted tests passed), then marked resolved.
All three new findings were independently confirmed and fixed.

## Changes

- Share managed runtime readiness validation between status and detector execution.
  Check before model hashing, cache reuse, request creation or subprocess execution.
  Either managed component requires READY; fully external configurations remain supported.
  Inspect configured and resolved paths to account for redirected Windows cache paths.
- Verify persisted review payloads against `review_sha256` before status consumes a verdict,
  an import replays, or revision context reads a review. Validate the stored submission
  and packet binding; corrupt review evidence returns the asset to audit/original fallback.
- Require `render_manifest_sha256` as a lowercase 64-character SHA-256 in the Pydantic
  submission model and generated JSON schema. Project schema remains 6.

## Validation

- The initial 10 regression cases failed before the source changes and passed afterward.
  Expanded coverage: 17 tests, including altered verdicts/receipts, absent digest, altered
  decision lists, required schema receipts, intact accept/reject/unresolved replay, and
  managed/external/mixed runtime execution plus cache behavior after removing READY.
- `scripts/check.ps1`: passed. Release metadata/generated schemas, Ruff and Mypy
  (34 source files) passed; **683 passed / 2 skipped in 98.21 seconds**.
- Skips: unavailable local WPF and Bodenschatz paper PDF fixtures.
- Doctor: all nine dependency imports and Poppler available; the existing, separately
  maintained local MinerU 3.4.5 runtime now reports `ok: true`. This follow-up did not
  install models or run a new real-model smoke test. Detector readiness tests use mocks.
- Full checks used Windows/Python 3.13.14 and `PIP_NO_CACHE_DIR=1` outside the restricted
  sandbox to avoid existing cache permissions. Local log: ignored `tmp/pr10-round2-full-check.log`.
- Entire PR `git diff --check 38918b0`: passed. Existing archived patch whitespace
  treatment remains scoped to its `.gitattributes` entry; patch bytes unchanged.
- Existing synthetic-render tests passed in the full suite, and the new corruption tests
  confirm altered reviews cannot expose candidate LaTeX in HTML. No fresh browser visual
  inspection was performed because this follow-up does not change rendering markup.

CHANGELOG and the fidelity contract were updated. No source PDFs, translations, generated
reading editions or local runtime maintenance files are included; `.claude/` is untouched.
CI results and the new review request are recorded in the PR after this commit is pushed.
