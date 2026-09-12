# PR #10 third review follow-up — 2026-09-13

Baseline: `8b458f9`. The preceding three fixes were rechecked and their discussions
resolved. All three new findings were confirmed and addressed.

## Changes and regression evidence

- Layout cache fingerprints now include worker SHA-256, configured/resolved interpreter,
  interpreter SHA-256, Python identity and installed distribution versions. The runtime
  metadata probe runs before cache reuse and failure returns unavailable. Tests reproduce
  stale reuse after worker/interpreter/version changes and after probe failure, then verify
  invalidation and stable cache reuse. The previous READY lifecycle tests remain in place.
- Table cells use the page/language-scoped inline renderer. Source and target regression
  cases check header/body links against the corresponding footnote definition ID. A replay
  of the original callback reproduces `#fn--1`; the fixed callback links correctly.
- Source-review receipts bind packet ID/hash, source, reviewer, decision and approval with
  `receipt_sha256`. Approval reads check the digest and packet identity, current source and
  structure guidance, page/fingerprint binding and the visual approval conditions.
  Tests cover rejected-to-passed mutation, altered reviewer/decision, missing digest,
  changed packet, invalid provenance even with a recomputed digest, and legacy-receipt
  invalidation followed by fresh visual review. Schema remains 6; no approvals are migrated.

Nine initial source/cache regression cases failed before the fix. Two table cases initially
needed a missing test confidence field corrected; the old callback was then explicitly
replayed to confirm the defect on both sides. The final new regression file has 17 cases.
An existing structure-profile fixture was supplied the required real packet kind/schema
so it continues testing stale guidance, rather than failing earlier on a malformed packet.

## Validation

- `scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 files),
  **700 passed / 2 skipped in 110.04 seconds**, and doctor (including ready layout runtime).
  Windows/Python 3.13.14; `PIP_NO_CACHE_DIR=1` used for existing cache permissions.

- Two existing real-PDF tests skip because WPF/Bodenschatz local fixtures are unavailable.
- Read-only probe of the existing managed runtime succeeded: Python 3.12.14, 97 installed
  distributions, MinerU 3.4.5. No model installation or real-model inference was performed.
- Entire PR `git diff --check 38918b0` passed. Archived patch bytes and `.claude/` untouched.
- No new browser inspection: the table regression checks actual generated HTML link/ID
  pairs, while full rendering/source/asset tests check the unchanged remaining renderer.
- Logs are in ignored `tmp/pr10-round3-*.log`; no production PDFs or review evidence included.

The PR records current-commit CI evidence and the next Codex review request after push.
