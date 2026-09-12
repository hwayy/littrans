# Changelog

All notable distributed changes to LitTrans are recorded here. Versions follow semantic
versioning and correspond to Git tags named `v<version>`.

## [0.6.0] - Unreleased

### Changed

- Keep bilingual HTML anchor targets visible below the fixed header on desktop and mobile.
- Recognize explicit English/Chinese decade equivalents and numbered CHAPTER headings in deterministic QA while retaining number, unit and acronym protection.
- Added document-specific `source probe` preparation with source-bound structure guidance in extraction records, review packets and batch context; stale profile imports are rejected.
- Preserved complete parent groups and caller/footnote spans when batch budgets are exceeded, including intervening footnotes.
- Unified source preparation around faithful native prose and original PDF/SVG/PNG assets,
  with source-bound coverage review before parallel transcription and translation.
- Separated structured-asset candidates and independent visual/render review from translation
  approval; unfinished representations retain an explicit original-image reading fallback.
- Moved per-host role model defaults out of code into `profiles/host-models.yaml`; `project init`
  copies them into `agent_models` for per-project confirmation (recommended: Codex `gpt-5.6-luna`
  at `max`, Claude Code `sonnet` at `high`), preserving the three translation audit lenses and
  configured external review.
- Added Claude Code as a supported coordinator host: `.claude-plugin` manifests and marketplace,
  `CLAUDECODE` host detection with 3/6 waves, `--host claude`, tool-restricted read-only reviewer
  agents and host documentation. Claude-hosted external review remains a later revision.
- Made the isolated layout detector (MinerU 3.4.5, PP-DocLayoutV2) a required preparation
  component: `doctor` reports `layout_runtime`, `layout install` provisions it, and
  `source prepare` refuses to run without it unless `--allow-missing-layout` is given.
- Improved source structure recovery: wrapped headings stay one unit and never own the
  following prose; figures/tables group with their captions and render as `<figure>`; bullet
  lists become list items; displayed lines that mix notation and prose keep their own position;
  page numbers merged into a text block are detached as omitted running material; bare vector
  rules are omitted from reading; equation tags such as `(ODE)` bind like numbers.
- Improved formula region ownership: bold single letters in prose are notation, quotation
  marks, joining hyphens and sentence punctuation are trimmed from formula edges, detector
  boxes shrink to the owned glyph ink, a trailing prose phrase is split off a displayed formula,
  and the precise glyph exporter accepts empty clip groups and filled-rectangle rules.
- Added `source render`, a readable HTML checkpoint of the verified source with the original
  assets inline, as the last check before batching; `--standalone` embeds the images so the
  single file can be shared with a reviewer.
- Preserved typography in the extracted source: italic and slanted text faces (CMTI/CMSL as well
  as style names) become emphasis, bold and italic runs continue across line breaks, whole-heading
  markers are dropped, ligature glyphs expand to their letters, TeX spacing accents compose with
  the letter they sit on (`ITÔ`, `Itô`), and kerns reported as narrow spaces are not word spaces.
- Grouped list items with the paragraph that introduces them and with each other; a bold run-in
  label (`EXAMPLE 1.`, `Proof.`, `2.1.4. Stochastic processes.`) or vertical white space opens a
  new paragraph even inside one PDF text block, and statement labels in bold or capitals start a
  statement group.
- Kept a displayed formula and the prose set beside it on its line (`... for all times t > 0.`) as
  one displayed unit with the formula asset and translatable text; words inside the notation
  (`sup` conditions, braces annotations) stay in the formula image, while a prose line the
  detector rectangle overshoots into returns to its paragraph.
- Treated large TeX operators encoded as control characters as ink, end-of-proof tombstones and
  plain numbers in the text face as text rather than notation, and gave displayed units the
  formula's geometry.
- `source probe` extends an existing structure profile with observations for pages not yet probed
  instead of refusing, returning it to draft until the rules cover the new pages.
- Cut the test suite from over twenty minutes on a machine with the layout detector to under two:
  tests stub the detector unless marked `layout_runtime`, and the synthetic reviewed projects
  are built once per session and copied.
- Introduced schema 6 and rebuilding older projects into a new directory with source/context/glossary
  only. Earlier extraction modes and exact-LaTeX pretranslation gates are no longer the workflow.
- Added the transcription skill, asset review role and offline MathJax reading contract.

### Fixed

- Reported an unresolved `{{asset:ID}}` reference by name during Markdown rendering, matching the
  existing bilingual HTML behavior, instead of aborting the render with an unlabeled lookup error.
- Reused the page's cached layout-detector result when a source review override re-prepares a
  page, so corrections no longer lose heading and block structure.
- Stopped treating every word of an all-caps heading as a protected acronym in deterministic QA;
  headings can be translated without appending the English words.
- Stopped treating the words of a bold all-caps run-in statement label (`**EXAMPLE 1.**`,
  `**WARNING ABOUT NOTATION.**`) as protected acronyms: new extractions no longer record them,
  and QA accepts a localized bold label (`**例 1.**`) for already prepared units.
- `workflow next` on a project without batches now says that batches must be created first
  instead of failing on an empty resume range.
- Labelled non-translatable equation units explicitly in audit packets so reviewers do not report
  the original-image reading content as an omission.
- Kept inline formulas that fell back to a raw mixed region inline in the reading edition instead
  of forcing block display and breaking the sentence.

## Historical changes before 0.6

The workflow descriptions below document released history, not current operating instructions.

## [Unreleased before 0.6]

### Changed

- Grouped weekly Dependabot minor and patch updates by ecosystem while keeping major-version
  updates separate for explicit compatibility review and maintainer-controlled merging.

### Fixed

- Made source continuation verification honor page geometry when a visually interposed block was
  appended later in unit storage order, while retaining reviewed-Markdown continuation checks.

## [0.5.0] - 2026-08-21

### Added

- Published LitTrans as an open-source GitHub marketplace under the MIT License, with Windows CI,
  issue and pull-request templates, a security policy, a code of conduct, and Dependabot upkeep.

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
