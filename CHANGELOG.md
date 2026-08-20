# Changelog

All notable distributed changes to LitTrans are recorded here. Versions follow semantic
versioning and correspond to Git tags named `v<version>`.

## [Unreleased]

## [0.5.0] - 2026-08-20

### Added

- Added Cursor host-subagent review imports with paired dry-run/result bindings, exact packet and
  page-evidence hashes, actual-model attestation, configured fallback matching, independent second
  opinions, durable reservations, and tamper/staleness rejection without nesting Cursor CLI.
- Added schema-v5 project snapshots, batch-local content-addressed work packets, compact fixed-wave
  status, safe packet pruning, and metrics that separate logical review calls from evidence rows.
- Recorded every external-provider attempt with raw output, failure class, duration, tokens, cache
  use, turns, and fallback lineage; added targeted format repair and crash-recoverable per-service
  OS locks.

### Changed

- Made `workflow next` host-aware: Codex waves remain capped at three batches, while Cursor defaults
  to six and supports up to nine; audit-lens assignments remain independently capped at three.
- Clarified that closure findings remain actionable after the combined initial revision pass and
  that typical wave counts never justify suppressing a valid fluency or style defect.
- Scoped internal audit invalidation and closure to each batch's real dependency set, skipped empty
  audit evidence, stabilized imported reviewer issue IDs, and made three-lens imports atomic.
- Reworked continuation around one initial audit, consolidated per-batch revision, a frozen wave,
  and one minimal closure; independent external pipelines can advance without blocking clean peers.
- Packaged the bilingual HTML template inside the wheel and made layout overrides validate their
  complete derived snapshot before replacing project files.

### Fixed

- Restored shared brief, style-guide, glossary, translation-memory, and adjacent-source context to
  translation packets; included every context input in packet identities and rebuilt malformed or
  stale cached packets instead of reusing them.
- Revalidated external-review context, scope, fingerprints, ancestry, model/effort labels, and
  effective suggested revisions before import or approval; stale provider results can no longer
  inject issues or approve units outside the reviewed snapshot.
- Made external-review imports, issue resolution, reviewer reservations, provider calls, render
  publication, layout overrides, review-set imports, and extraction asset replacement transactional
  across interruption and concurrent project activity.
- Preserved active batch-series lineage, cross-batch dependency closure, seam context, render
  provenance, legacy render ownership, continued-table reader notes, and current manifest selection
  when resuming long-running projects such as WPF45.
- Made packet pruning conservative for legacy and schema-v5 manifests: unknown batches, incomplete
  batch mappings, missing fingerprints, or partially imported lenses are never deleted.
- Hardened PDF code/prose classification for C#, XAML, body-font listings, page breaks, glued prose
  lead-ins, same-page fragments, ambiguous dotted calls, and proportional `Monotype` fonts.
- Bound host review provenance to the final packet and truthful attempt telemetry, and restored or
  released dry-run reservations on every import, rendering, version-probe, and persistence failure.
- Serialized default `bNNN` ownership and rolled back interrupted multi-file render publication so
  concurrent or failed first renders cannot strand or overwrite another batch's output.

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
