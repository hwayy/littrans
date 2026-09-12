# PR #10 fifth review follow-up — 2026-09-13

Baseline: `1f9860b`. Its five findings were rechecked and resolved. Three new
findings were confirmed and repaired; the initial five regression cases reproduced
them before the implementation changed.

- Workflow asset branches forward the resolved host into `build_asset_packet` for
  transcription and asset audit. Direct `assets packet --host` uses the same resolver.
  Tests cover both branches and workflow/direct CLI under Codex with a Claude policy.
- Markdown emits matching unique footnote calls and definitions derived from stable
  unit IDs, avoiding reused page numbers. Explicit cross-page references and legacy
  same-page lookup are supported. Code/math literals are preserved; multi-line and
  asset-expanded note content is indented as one definition. Continued table fragments
  with independent footnotes retain their scopes instead of being merged.
- Source override unit IDs are checked against retained page ownership and all explicit
  decisions before changes; generated replacement IDs are checked again before registry
  publication. Collision regressions verify the original units file is unchanged.

The final new regression file contains 10 cases. Existing CLI forwarding tests now
accept and assert the default host argument while retaining revision-note checks.

Final fresh-process `scripts/check.ps1` passed: release metadata/schema, Ruff,
Mypy (34 source files), **726 passed / 2 skipped in 116.04 seconds**, and doctor
including the existing ready layout runtime. Windows/Python 3.13.14 with
`PIP_NO_CACHE_DIR=1` for existing cache permissions.

Two local WPF/Bodenschatz PDF fixtures are unavailable and skipped. Project schema
remains 6. No production data, model installation/inference or new browser inspection
is part of this round. Markdown regressions inspect actual emitted calls/definitions,
uniqueness and indentation. `.claude/` and archived patch bytes are untouched.

Full PR `git diff --check 38918b0` passes. Local logs remain in ignored
`tmp/pr10-round5-*.log`. CI and the next Codex review are recorded in the PR.
