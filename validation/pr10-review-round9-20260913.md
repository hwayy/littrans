# PR #10 ninth review follow-up — 2026-09-13

Baseline: `12951a4`. Both new findings were reproduced and repaired.

- HTML inline parsing excludes escaped footnote calls and handles backslash inline
  and display math before interpreting calls. Adjacent real calls remain linked;
  code and dollar math retain their existing behavior.
- Original-image PNG fallback URLs are stored in a dedicated data attribute used
  by the onerror handler. Standalone source rendering embeds both primary src and
  fallback attributes as data URIs, including the PNG bytes.

Six regression cases pass; four failed on the baseline. A separate Edge/Playwright
CLI check served only a copied standalone HTML in an isolated directory. The original
SVG loaded, then a deliberately invalid SVG triggered the real onerror handler. The
PNG data URI loaded with naturalWidth 135, and the handler cleared itself. The server
received only the HTML request and a harmless missing favicon request; no image sidecar
was requested. The screenshot was inspected and the formula remained visible.
Existing PDF/original-page links are navigation references, not embedded image resources.

Local visual artifact: `output/playwright/round9/png-fallback.png` (not committed).
No production PDF, translation or approval was used. Final complete checks and CI
are recorded below and in PR #10.

Final `scripts/check.ps1`: release metadata/schema, Ruff, Mypy (34 source files),
**775 passed / 2 skipped in 126.55 seconds**, doctor (`ok: true`). The two skips
are the unavailable local WPF/Bodenschatz PDF fixtures. Whole-PR whitespace checks
pass. Project schema remains 6; `.claude/` was not modified.
