# Changelog

All notable distributed changes to LitTrans are recorded here. Versions follow semantic
versioning and correspond to Git tags named `v<version>`.

## [Unreleased]

### Added

- When Cursor is the coordinating host, a local host subagent can review an isolated `cursor-cli`
  dry-run packet and record the result with paired `--from-result` and `--from-dry-run` inputs,
  without nesting Cursor CLI. The CLI binds the result to the current batch, model, translation
  fingerprint, review scope, and packet hash. Required second opinions use a separate dry-run and
  import. Claude Code and Antigravity reviews still use their CLIs.

### Changed

- Clarified continuation closure: the single combined revision pass consolidates the initial
  three-lens findings only. Coordinators must still accept remaining fluency defects found on
  closure, including follow-ons from that revision. Do not reject a valid style finding solely to
  avoid another cycle, and do not treat typical-wave counts as a cap against that extra revision.
- Made `workflow next` host-aware. Codex keeps default 3 / hard max 3. Cursor defaults to 6 and
  allows up to 9 so more local writers can run in parallel. Status, packets, metrics, and
  combined `--batch-ids` renders accept up to nine consecutive batches; audit-lens assignment
  still chunks at most three consecutive batches per reviewer.

### Fixed

- Bind Cursor host-subagent result JSON to the exact dry-run packet before recording an
  external approval, and reject missing, stale, tampered, or cross-packet bindings.
- Require the trusted Cursor host coordinator to attest the actual model when importing a
  host-subagent result, and reject missing or mismatched model labels instead of treating the
  configured requested model as runtime evidence.
- Record a supplied host-subagent result even when an earlier external run is already approvable,
  so a newly reported substantive defect cannot be silently discarded.
- Serialize ownership checks and atomic writes for default `bNNN` render outputs so concurrent
  batches cannot overwrite one another.
- Split short prose lead-ins such as `For example:` and `Use this:` from an immediately glued
  method or control-flow listing.
- Classify C# and XAML listings that use the body font or omit a trailing semicolon, including
  `while`/`for` snippets, method fragments, and `xmlns` continuations, as non-translatable code.
- Leave ambiguous dotted calls such as JavaScript `console.log()` and Java
  `System.out.println()` language-neutral instead of labeling them as C#.
- Do not join a prose paragraph to a following code listing across a page break.
- Merge same-page code fragments with the book's ~13pt listing gap, and restore indented listing
  text from the PDF when a layout override changes a unit's kind to code.
- Split a PDF text block that glues a body sentence onto the first lines of a C# or XAML listing
  so the prose stays a paragraph and the listing can merge with the following code fragment.
- Refuse to overwrite a short `bNNN` render owned by a different batch unless `--name` is supplied.
- Enforce the three-consecutive-batch cap in audit packet creation while keeping Cursor waves,
  translation packets, status, and combined rendering at up to nine batches.

## [0.5.0] - 2026-08-13

### Added

- Added schema-v5 project snapshots, batch-local content-addressed work packets, compact fixed-wave
  status, safe packet pruning, and metrics that separate logical review calls from evidence rows.
- Recorded every external-provider attempt with raw output, failure class, duration, tokens, cache
  use, turns, and fallback lineage; added targeted format repair and per-service process locks.

### Changed

- Scoped internal audit invalidation and closure to each batch's real dependency set, skipped empty
  audit evidence, stabilized imported reviewer issue IDs, and made three-lens imports atomic.
- Reworked continuation around one initial audit, consolidated per-batch revision, a frozen wave,
  and one minimal closure; independent external pipelines can advance without blocking clean peers.
- Packaged the bilingual HTML template inside the wheel and made layout overrides validate their
  complete derived snapshot before replacing project files.

### Compatibility

- Added lossless schema-v4-to-v5 migration. Existing evidence remains usable until locally
  invalidated; legacy packet directories are reported but never deleted automatically.

## [0.4.0] - 2026-08-13

### Added

- Added a Cursor plugin manifest, marketplace catalog, and local writer/reviewer subagents so the
  same plugin tree installs on Cursor without changing the Codex marketplace path.
- Documented Codex and Cursor install, update, and local-only subagent rules.

### Changed

- Rewrote skill descriptions and host invocation wording so workflows are agent-neutral. Codex
  `$skill-name` prompts in `agents/openai.yaml` are unchanged.
- Cursor audit reviewers stay read-only and return JSONL for the parent to persist; consumer
  Cursor install clones into `littrans` before creating the local plugin junction.

## [0.3.1] - 2026-08-13

### Fixed

- Added compatibility with Antigravity CLI 1.1.12 success envelopes while preserving legacy
  direct structured-review results.
- Failed immediately on non-success Antigravity statuses and rejected missing, invalid, or
  unexpectedly extended structured outputs without weakening actual-model verification.

## [0.3.0] - 2026-08-11

### Added

- Added schema-v4 page verification receipts, unit-level audit runs, workflow packet manifests,
  external-review usage metadata, and a lossless `project migrate --to 4` command.
- Added three-batch workflow selection and packet generation, review-set import, workflow metrics,
  exact multi-batch rendering, and the `continue-literature-translation` coordinator skill.

### Changed

- Made translation submission semantic: metadata-only or identical resubmissions no longer create
  revisions, history entries, status changes, or evidence invalidations.
- Reused unchanged page verification and audit evidence while invalidating changed units and their
  continuation, structured-region, adjacency, and seam dependencies precisely.
- Reduced large schema-v3 migration previews to one shared project snapshot and skipped expensive
  fingerprint reconstruction for manifests that contain no legacy evidence.
- Scoped model packets to relevant approved terms and at most six current approved translation
  memories, with adjacent examples preferred.
- Added full-to-incremental external review selection, Claude stdin prompt delivery with file-mode
  fallback, Antigravity JSON Schema output, and normalized duration/token/cost recording.
- Kept Claude stdin prompt delivery feature-gated off after the six-batch shadow A/B missed a
  seeded major technical defect. Production review continues to use file delivery; the other
  efficiency improvements are unaffected.

### Compatibility

- Preserved all v0.2 commands and project content. Schema-v3 projects require the documented
  one-time migration; no translation, issue, revision, or approval state is rewritten.

## [0.2.2] - 2026-07-30

### Fixed

- Merged list items that continue across PDF pages into one logical item in Markdown and
  bilingual HTML renders.
- Recorded external-review timeouts as failed review runs instead of losing the attempt history.
- Preserved sidebar context and joined sidebar body fragments across page boundaries.
- Normalized translated Chinese figure captions consistently during QA, external review, and
  rendering.

## [0.2.1] - 2026-07-29

### Added

- Established the private Git-backed `littrans` marketplace as the stable distribution source.
- Added controlled batch rendering and external review gates.
- Added structured support for tables, code, callouts, sidebars, reader notes, and cross-page
  continuations.
- Added repository-local validation and a documented manual release workflow.

### Changed

- Aligned the plugin manifest, Python package, and runtime package version at `0.2.1`.
- Separated the stable release identity from local Codex cachebuster versions.

### Fixed

- Preserved translation and rendering semantics across structured regions and page boundaries.
- Rejected placeholder external-review evidence and recovered source units nested in extracted
  tables.
