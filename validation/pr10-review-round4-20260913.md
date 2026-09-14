# PR #10 fourth review follow-up — 2026-09-13

Baseline: `b55570b`. Its three findings were confirmed fixed and resolved. The next
review raised five substantive findings, all reproduced and addressed in this round.

## Repairs and scope

- Source coverage HTML is hashed before packet identity is computed. The packet binds
  its report hash and original-page/overflow-image manifest; review submissions and
  stored receipts echo `visual_report_sha256`. Import and approval reads verify the
  report and images. URLs are relative so a moved workspace keeps the same evidence.
  Rebuilding corrupted evidence creates a new packet ID without repairing old approval.
- CMR superscript calls are matched to detected note numbers using raised geometry and
  nearby prose. Mathematical-font bases and numeric exponents remain excluded.
- `workflow packet --host` reaches the same coordination-host resolver and translation/
  revision policy validation, including recursive audit construction. Auto stays default.
- Footnote calls resolve `footnote_refs` to stable source/target definition anchors.
  Page-scoped outputs include referenced definitions from other pages. Tables, emphasis,
  figure labels and image-language companions use the same scoped inline renderer.
  Legacy page/number anchors remain available as aliases.
- Source overrides reject IDs owned by another page and duplicate IDs within the import,
  before authoritative changes. A second check prevents generated registry collisions.
  Same-page boundary repairs retain stable IDs: existing content fingerprints and fresh
  visual review invalidate obsolete evidence. The review's stricter suggestion to require
  `preserve_asset_id` for every reuse would break this established repair workflow;
  `preserve_asset_id` continues to reuse unchanged crops on their own page.

## Validation

Ten initial regression cases failed before the repair. The final new test file contains
16 cases covering both import/approval artifact damage, new-identity recovery, explicit
Claude/Cursor policy selection and CLI forwarding, CMR positive/exponent negative cases,
cross-page table links and actual page-scoped output, image-language companions and
cross-page/within-import asset collisions with unchanged authoritative registries.

Existing synthetic fixtures now use the public source-review packet/template rather
than hand-written incomplete packets. The established unchanged-translation boundary
repair regressions caught the overly strict initial ID check; both pass with the
page-ownership check while collision tests still fail closed.

`scripts/check.ps1` passed on the final code: release metadata/schema, Ruff, Mypy
(34 source files), **716 passed / 2 skipped in 109.83 seconds**, and doctor including
the existing ready layout runtime. Windows/Python 3.13.14; `PIP_NO_CACHE_DIR=1` used
for existing cache permissions.

- Two unavailable local WPF/Bodenschatz PDF fixtures are skipped.
- Entire PR `git diff --check 38918b0` passes; archived patch bytes unchanged.
- Project schema remains 6. Legacy source reviews lacking report bindings require new
  visual review, with no automatic approval migration. Contract and host docs updated.
- No model install/inference or new browser visual inspection was performed. Regression
  checks inspect actual generated HTML links/definition IDs and report/image digests.
- `.claude/` untouched. Logs remain in ignored `tmp/pr10-round4-*.log`; no production
  source PDFs, translations or reviewer evidence are committed.

Current-commit CI and subsequent Codex feedback are recorded in PR #10.
