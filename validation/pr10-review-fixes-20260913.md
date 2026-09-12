# PR 10 review-fix validation — 2026-09-13

This follow-up addresses all eight review findings against `b091662`. Project schema
remains 6 and the shared unreleased version remains 0.6.0.

## Changes and regression evidence

- Asset packets and imports enforce math/latex, table/table and code/code. Figures
  and mixed regions remain original-image fallbacks; old mismatched candidates cannot
  stay verified. Mixed regions require reviewed source classification before transcription.
- Audit packet identity binds a manifest of comparison HTML, all copied MathJax files,
  and original SVG/PNG/PDF dependencies. Imports require both HTML and manifest receipts;
  status rechecks the same files. Missing manifests require fresh audits. A damaged
  render is rebuilt under a new packet identity without restoring the old approval.
- Markdown serializes verified tables/code with original references; standalone text
  serialization is tested without opening a new text-candidate admission path.
- Source-render names use the existing safe-name policy. Rebuild copies and hash-checks
  the PDF into a staged new workspace, records a relative source path and publishes only
  after initialization succeeds. Historical approval evidence is not copied.
- Completed reading scopes expose bounded optional transcribe/asset-audit tasks.
- Multi-digit superscript calls emit one marker per call and deduplicated footnote
  dependencies; regression cases include 1/10/12, repeated calls, normal-size numbers,
  separated digits and unmatched longer numbers.
- Managed layout status requires the readiness marker. Install attempts remove stale
  markers and only restore them after successful smoke tests; missing model weights
  are downloaded on retry. External runtimes remain supported.

The initial 11 asset-boundary/dependency regressions failed on the pre-fix implementation.
Additional pre-fix checks reproduced source-copy, readiness, multi-digit footnote and
completed-queue failures. Final tests include acceptance before/after dependency mutation,
missing files/receipts, altered manifests and paths, legacy evidence, fresh-review recovery,
Markdown escaping, portable rebuilt projects, source output containment and install retries.
Two old fixtures that submitted figure candidates or LaTeX for code now obey the kind contract.

## Final checks

- `scripts/check.ps1`: passed; release metadata and generated schemas matched, Ruff passed,
  Mypy checked 34 source files, **666 tests passed / 2 skipped in 88.57 seconds**.
- Skips are existing unavailable local WPF and Bodenschatz PDF fixtures.
- Python 3.13.14 on Windows; all nine doctor dependency imports and Poppler tools available.
- Initial restricted runs encountered Windows permissions on pre-existing pip/pytest caches.
  The successful complete script ran outside the restricted sandbox with `PIP_NO_CACHE_DIR=1`.
- `git diff --check 38918b0`: passed for the entire PR plus these changes.
- Real Edge/Playwright check of synthetic output: MathJax rendered, original images loaded,
  original PDF link retained, table/code contents intact, no page JavaScript errors. Desktop
  (1100 px) and mobile (390 px) screenshots were inspected. Blocking MathJax retained the
  original-image fallback. This is renderer verification, not a real translation approval.

## Runtime and evidence limits

The existing local managed layout environment has MinerU 3.4.5 and model files but no
READY marker. Doctor now correctly reports `ok: false` with an incomplete-smoke-test
reason. No real model installation or smoke test was performed in this task; install
failure/retry coverage uses isolated mocks. The check script reports this diagnostic but
currently does not fail on it. This result must not be described as a healthy real layout runtime.

Local logs and synthetic browser artifacts are under ignored `tmp/`; no source books,
translations, credentials, or production review evidence are included in this change.
