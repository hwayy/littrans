# Fidelity workflow and recovery

Schema 6 uses one source preparation path. Native prose and layout regions produce immutable
source-owned `{{asset:ID}}` references; original vector PDF/SVG and high-resolution PNG evidence
remain available throughout the workflow. A detector warning requires a source decision, not an
automatic formula-recognition retry. Project schema is 6 throughout this document.

Contents:

1. [Independent states](#independent-states)
2. [Source preparation](#source-preparation)
3. [Source review packets, decisions and overrides](#source-review-packets-decisions-and-overrides)
4. [Narrow original-glyph corrections](#narrow-original-glyph-corrections)
5. [Footnotes and logical containers](#footnotes-and-logical-containers)
6. [Batches](#batches)
7. [Translation records](#translation-records)
8. [Asset representation](#asset-representation)
9. [Deterministic QA](#deterministic-qa)
10. [Audit coverage](#audit-coverage)
11. [Workflow coordination](#workflow-coordination)
12. [Rendering](#rendering)
13. [Resume and recovery](#resume-and-recovery)

## Independent states

- **Source fidelity**: all selected content has reviewed ownership, reading order and complete
  boundaries, bound to the source fingerprint.
- **Translation**: submitted prose, deterministic QA, three independent audit lenses, configured
  external review and optional explicit human approval.
- **Asset representation**: original image, structured candidate, independent visual/render
  review, reliable candidate or original-image fallback.

The second and third states advance independently after source fidelity. Transcription is
optional enhancement and may be initiated at any later time, including after the reading edition
is complete. `workflow next` and `ready_tasks` schedule translation work; `optional_asset_tasks`
exposes the independent enhancement queue. `complete` means the reading workflow is complete,
while `assets_complete` separately reports enhancement progress.

Missing LaTeX never blocks an otherwise reviewed translation. Missing understanding or source
content does: nonempty recorded translation `uncertainties` block QA for the affected unit and its
semantic dependencies; resolve the actual understanding problem and resubmit before approval.
Merely unfinished LaTeX belongs to asset progress and must not be recorded as an unresolved
translation-understanding problem. Source/content/ownership changes invalidate affected translation
dependencies; a display-only change invalidates rendering evidence rather than unrelated prose audits.

## Source preparation

### Layout runtime

Managed layout components require a successful smoke-test READY marker both for status checks and
before detector execution or cached result reuse. A managed installer without a previous READY
marker redownloads the model snapshot even when its config and main weight file already exist.
Fully external interpreter and model configurations (`LITTRANS_LAYOUT_PYTHON`,
`LITTRANS_LAYOUT_MODEL`) retain their external-runtime exemption.

Layout cache identities bind the worker SHA-256, configured/resolved interpreter, interpreter
SHA-256, Python identity and installed distribution versions as well as images and weights. An
unsuccessful runtime metadata probe cannot reuse cached layout evidence. Worker results are
published atomically; unreadable or incomplete cached JSON triggers recomputation, and malformed
worker results (a page without a list-valued prediction) report unavailable rather than being
accepted. Source-review overrides reuse the page's recorded layout result, skip unreadable
unrelated caches and explicitly degrade to `unavailable` when no valid matching layout remains.

### Document-specific preparation

Run `source probe PROJECT --pages PAGES` before a new scope's extraction. Complete the
source-bound profile using [document-structure.md](document-structure.md). Source preparation
records its hash in new page ledgers; review packets include the profile and reject imports after
its contents change. Batch context includes the same document-specific handling rules. The
generic extractor remains a proposal generator: agents apply document-specific decisions using
the supported source overrides below, then inspect fresh coverage evidence. A profile does not
establish source fidelity or silently re-extract already reviewed pages.

Container kinds are open-ended, including examples, exercises, cases, algorithms or book-specific
forms. An enclosing container can include multiple author paragraphs, displays, lists and proofs.
Keep those internal boundaries: `parent_id` expresses scope, not a command to concatenate every
child. A paragraph around a display remains one logical scope; a following “where” clause must
not become an unrelated batch. Continuation across pages requires source indentation and context,
not punctuation alone.

### Prepared units and assets

Preparation splits a native block that contains a display formula into prose chunks and one
`equation` unit per display (`<block>-displaypartN` IDs); footnote links follow the chunk that
still carries the call, and an inconsistent footnote graph is rejected before anything is
published. Newly prepared assets use content identity version 2, binding original fragments,
formula conditions, kind, display and grouping state. Legacy version 1 remains readable;
preparation or semantic overrides create version 2 identities and require current
source/translation evidence.

A printed equation label such as `(1.6)` beside a display is bound to the unit's
`equation_number` and removed from `source_text`; the checkpoint HTML shows it in the unit meta
line and the Markdown/HTML renderers re-emit `(N)` themselves, like heading and list markers. A
label that stays in prose belongs to a block that preparation could not bind to a display asset.

Original glyph paths are measured from the page SVG to size assets, including pages MuPDF
wraps in a page-sized clip group (CropBox differs from MediaBox). Glyphs that still cannot be
measured keep their font metric box and the owning region records `ink-bounds-unmeasured` in
its provenance, so a crop that truncates a stretched delimiter is traceable rather than silent.

Words hyphenated across a line end are rejoined only when the rest of the document does not
print that compound more often than the joined word: `well-` / `known` stays `well-known` in
a book that prints `well-known` mid-line, while `proba-` / `bility` becomes `probability`.
A suspended hyphen inside a line (`pre- and post-processing`) is never altered.

Source authority transactions snapshot the unit and asset registries, translations, page
canvases, ledgers and receipts, and restore them on any error or user interruption. Source page
canvases are atomically published and included in that rollback. Incomplete original asset
caches are regenerated when their evidence receipt is absent.

## Source review packets, decisions and overrides

### Packet and receipt bindings

`source review-packets` writes `packets/source-<hash>/packet.json`, `review-template.json` and
`coverage.html`. Coverage HTML and its referenced page images are bound by the packet
`visual_report` manifest; relative image URLs keep reports portable. A damaged report is rebuilt
under a new packet identity and cannot silently restore prior approval.

Source-review receipts bind the decision, reviewer, source, page fingerprint and original packet
identity/hash with `receipt_sha256`. Submissions must echo `visual_report_sha256` after
inspection, and receipts retain it. Approval consumers (`source verify`, batch creation,
workflow coordination) verify the receipt and packet, then recheck the visual decision
conditions. Keep the original packet available. Legacy receipts without these bindings require a
fresh visual review import; no automatic approval migration is performed.

### Decision fields

Each entry of `pages` in the submitted review carries the packet page's `page` and `fingerprint`
plus the attestation flags from the template: `viewed_original`, `coverage_complete`,
`boundaries_complete`, `reading_order_correct`, `grouping_checked`, and, when the ledger requires
them, `layout_fallback_checked` (layout not `ok`), `overflow_canvas_checked` (a page canvas
override) and `formula_conditions_checked` (declared formula conditions). A page passes only
when every required flag is `true`, `issues` is empty, no `override` is present and no
`math`/`mixed-region` asset still owns six or more ordinary words (`recoverable-prose-in-image`).
A `mixed-region` image is never textual coverage of recoverable paragraphs: split the source
region and obtain a fresh packet.

Overrides are applied before approvals. Decisions whose dependency fingerprints changed because
another decision in the same import corrected a page appear in `deferred_pages`; corrected pages
appear in `changed_pages`. Both require a new packet and review, never an immediately stale receipt.

### Override contract

A decision may carry `override` to correct that page. Only the fields below are supported; the
transaction rolls back on any violation.

```json
{
  "page": 12,
  "fingerprint": "<packet page fingerprint>",
  "viewed_original": true,
  "notes": "Display (3.2) was cut; caller/footnote link repaired.",
  "override": {
    "regions": [
      {"id": "eq-3-2", "kind": "math", "bbox": [72.0, 310.5, 318.2, 342.0], "display": true,
       "glyph_ids": ["b3-l4-s0-c0", "b3-l4-s0-c1"],
       "formula_conditions": [{"glyph_ids": ["b3-l4-s2-c0", "b3-l4-s2-c1", "b3-l4-s2-c2"], "source_text": "and"}]},
      {"id": "fig-3-1", "kind": "figure", "fragments": [{"bbox": [72, 400, 540, 610]}, {"bbox": [72, 620, 540, 700]}]},
      {"preserve_asset_id": "a-p0012-1f2e3d4c5b6a", "grouping_pending": false}
    ],
    "units": [
      {"unit_id": "p0012-b3", "kind": "paragraph", "bbox": [72, 280, 540, 300], "parent_id": "p0012-b3",
       "source_markdown": "Hence[^2] the norm {{asset:a-p0012-1f2e3d4c5b6a}} satisfies", "footnote_refs": ["p0012-b9"]},
      {"unit_id": "p0012-b3-displaypart2", "kind": "equation", "bbox": [72, 310, 320, 342], "parent_id": "p0012-b3",
       "source_markdown": "{{asset:eq-3-2}}", "equation_number": "3.2"},
      {"unit_id": "p0012-b3-displaypart3", "kind": "paragraph", "bbox": [72, 350, 540, 370], "parent_id": "p0012-b3",
       "source_markdown": "for all t."},
      {"unit_id": "p0012-visual-fig-3-1", "kind": "figure", "bbox": [72, 400, 540, 700],
       "source_markdown": "{{asset:fig-3-1}}"},
      {"unit_id": "p0012-b9", "kind": "footnote", "footnote_number": "2", "bbox": [72, 700, 540, 720],
       "parent_id": "p0012-b9", "source_markdown": "See the remark after Lemma 2."}
    ]
  }
}
```

- `page_canvas_bbox: [0, 0, width, height]` expands an unrotated origin-zero page in memory
  (see [Formula-contained language and page overflow](#formula-contained-language-and-original-page-overflow)).
- `regions` replaces the detector/native proposals for the page. Each region names `kind`
  (`math`, `table`, `code`, `figure` or `mixed-region`) and either a single `bbox` in PDF points
  or `fragments: [{bbox, glyph_ids?}, ...]` for one logical asset with several ordered fragments
  on the same page (cross-page elements use continuation links instead). Optional fields:
  `id` (`[A-Za-z0-9][A-Za-z0-9._-]*`, default `a-p<page>-<content hash>`), `glyph_ids` (explicit
  native glyph ownership; see the glyph corrections section), `display`, `grouping_pending`,
  `provenance` and `formula_conditions`. `preserve_asset_id` reuses an unchanged existing asset
  of the same page and may only change `kind`, `display`, `grouping_pending` or
  `formula_conditions`. A region may not import `latex`. Glyph ownership may not overlap between
  assets, and asset IDs may not collide across decisions or with assets of another page.
- `units` replaces the page's source units. Each unit needs `unit_id`
  (`[A-Za-z0-9][A-Za-z0-9._-]*`, unique across the project), `source_markdown` (prose with
  `{{asset:ID}}` placeholders and `[^n]` footnote calls) and `bbox`; optional `kind` (default
  `paragraph`; `footnote`, `heading`, `list_item`, `caption`, `equation`, `figure`, `table`,
  `note`, `bibliography`), `equation_number`, `footnote_number`, `footnote_refs`, `parent_id`,
  `continues_from_previous`, `continued_to_next`, `render_policy` (`include`/`omit`) and
  `translatable`. Across the page's units every page asset must be referenced exactly once. When
  `units` is omitted the units are re-derived from the regions with the normal structure
  assembly and inline-fragment coalescing.
- Footnote relationships are validated against the retained and replacement units together:
  unknown, non-footnote or duplicate targets and call numbers that do not match the referenced
  definitions reject the whole import.
- Same-page boundary repairs may retain stable IDs; changed content fingerprints invalidate old
  evidence and require fresh source review. Translations of removed unit IDs are retired to
  `translations/source-retired.jsonl` and removed from the current ledger in the same rollback
  transaction; batches containing changed units receive audit invalidations.

### Formula-contained language and original page overflow

A complete displayed cases formula may contain condition words such as “and … is odd”. A
reviewed region can declare `formula_conditions: [{glyph_ids: [...], source_text: "..."}]`. Each
entry must match owned native glyphs in native order on one visual line. The source gate still
checks all undeclared prose. It additionally requires the independent page review's
`formula_conditions_checked`; the asset remains math, its source unit becomes translatable, and
translation QA requires a Chinese companion and rejects a no-language attestation. Empty
declarations are omitted from serialization to preserve existing source fingerprints.

An explicit page override `page_canvas_bbox: [0, 0, width, height]` may reveal preexisting
content-stream glyphs outside an unrotated PDF page's current box. Only the temporary in-memory
document is expanded. The original page image remains unchanged; the ledger adds a hashed
overflow image and the original page box, and original-context packets require both images.
Review must explicitly attest `overflow_canvas_checked`. Recovered glyphs participate in normal
ownership, immutable image fragments and source verification. No OCR or replacement symbols are
introduced.

## Narrow original-glyph corrections

When a region includes neighboring prose, a source reviewer may explicitly name its existing PDF
`glyph_ids`. The narrow glyph exporter copies the selected original PDF vector paths and supported
nearby horizontal rules into the reading SVG and model PNG; it performs no OCR, character
substitution or LaTeX inference. The separate original PDF fragment remains raw-region evidence.
This correction requires inspected ownership and a new packet-bound source review; it is not an
automatic formula recognizer.

Bounds include the actual visible vector ink, because font-reported boxes can put accents or
italic overhangs outside their nominal rectangle. SVG dimensions, PNG export and baseline
placement use the expanded bounds. Unmapped glyphs, unsupported SVG groups/transforms or vector
primitives fail closed. Retain the previous original region, or issue a new complete raw-region
correction without explicit glyph filtering, and review that fallback; never approve a silently
incomplete filtered image.

## Footnotes and logical containers

Footnotes keep `footnote_number` on the footnote unit and `footnote_refs` on its calling unit.
References contain stable source unit IDs, each resolving to a `footnote` unit, without
duplicates. Explicit footnote call numbers must match the unique referenced definition numbers;
literal escaped/code/math syntax is excluded. Preserve the number and relationship separately
from prose. Source review and audit dependencies include both caller and footnote, including
cross-page links; a changed footnote invalidates its dependent caller's evidence. HTML provides
links to the original footnote unit anchors and displays its recorded number.

Reviewed `parent_id` groups retain a theorem, lemma, proposition, definition, corollary or claim
together with enumerated clauses and display equations. Preparation recognizes explicit statement
labels and enumerated children conservatively; a heading, proof or new indented prose ends the
inferred statement. Source review must confirm ambiguous and cross-page boundaries. Children
retain stable IDs, equation numbers and markup. Batching keeps a whole parent group together and
bilingual rendering presents contiguous children in one row, preserving child anchors and
paragraph/list boundaries. Parent groups participate in source evidence and audit dependency
closure, so changing one clause requires rechecking the whole statement.

Continuation dependencies skip omitted running material and footnotes and require physically
adjacent PDF pages. A fragment at the boundary of a noncontiguous page selection remains a
fragment; it must not be joined to the next extracted page across a gap.

## Batches

`batch create PROJECT --pages PAGES` cuts verified pages into batches of about 900 source words
and a soft limit of 60 assets at logical boundaries; `--unit-ids` selects an explicit complete
unit set and `--untranslated-only` limits the editable scope to units without a current
translation. Both record `frozen_scope: true`. Refresh keeps their selected IDs rather than
filling interior gaps; removed IDs or newly cut logical groups require an explicit new selection.
Legacy/range manifests retain interval refresh behavior.

Untranslated-only manifests retain complete selected parent groups. Their `read_only_unit_ids`
are context, excluded from `translatable_unit_ids` and submission; refresh preserves that boundary
and reopens read-only units whose translations are missing or stale. Packet source and context
identify both scopes. Batch output schemas come from the submission model and include
`image_evidence` and `asset_translations`; refresh existing batches to update their emitted schema.

## Translation records

Translation records retain their unit's `source_hash`, references and an `image_evidence` map of
inspected original image path to SHA-256 from `original-images.json`. Supplementary translations
of image-contained language live in `asset_translations`, keyed by `asset_id`, using
`target_text`, `target_table` or `figure_labels`. Declare `language_present: false` with
explanatory `notes` only for an asset with no translatable natural language. Asset candidate
content never replaces the source reference ID. Do not put raw LaTeX in translated image
companions; preserve references and submit mathematical candidates through the independently
reviewed asset channel. Companion text, table cells and label mappings cannot contain asset
placeholders or live footnote calls; keep calls in the main translation and escape literal
notation or use code literals.

A resubmission that is semantically identical to the current record keeps its revision. When
only the `source_hash` binding changed, the record is rebound as `revised` and its audits are
invalidated. When only `image_evidence` changed, the receipt is updated in place: the translated
content is untouched, audits stay valid, and QA (which binds the receipt) simply becomes stale.

## Asset representation

### Packets

Structured candidate formats are bound to source kind: `math` accepts `latex`, `table` accepts
`table`, and `code` accepts `code`. Figures and `mixed-region` assets remain original images; use
a source review correction to establish a more precise kind before requesting transcription.
Free text is not a replacement format for these kinds. Asset task scope includes semantic
dependencies outside the requested batch, matching QA.

Asset-audit packets bind `render_manifest` (relative render-directory paths to SHA-256) and
`render_manifest_sha256` into the packet identity. The manifest covers the comparison HTML,
copied MathJax runtime and original SVG/PNG/PDF files. Review submissions must echo both
`render_artifact_sha256` and `render_manifest_sha256` from the packet after inspecting the actual
artifact. Imports and subsequent status queries verify all dependencies. Old packets without this
manifest require a new audit and cannot retain verified status; their candidates and history
remain available. Rebuilding a damaged render creates a new packet identity and requires a fresh
review, never silently repairs an old approval.

### Submission and review

Submit transcription through `assets submit PROJECT INPUT`; the envelope includes `packet_id`,
`author_task_id`, actual `model`, `reasoning_effort`, `image_evidence`, `candidates` and available
`usage` (otherwise `null`). Candidates name `asset_id`, `format` and `content`; `status` is
`candidate` or `unresolved`, with `notes` and `semantic_uncertainty` as needed. LaTeX content is a
math body without dollar delimiters; table content uses a rectangular `rows` array of cell
strings. Both candidate and review envelopes record actual viewing in `image_evidence` using the
packet's `required_images` path/hash map.

An asset reviewer has a different `reviewer_task_id` and returns `render_artifact_sha256` and
`render_manifest_sha256` plus one decision per asset, with `candidate_sha256`, `verdict`
(`accept`, `reject` or `unresolved`), `visual_checked` and `render_checked`; import using
`assets import-review PROJECT INPUT --confirm-visual-review` only when those checks were
performed. Stored review payloads must match their `review_sha256` before decisions are consumed,
imports replayed or revision context built; corrupt evidence cannot grant verified status. An
indexed review that cannot be validated blocks QA until renewed independent review; rebuilding its
audit packet uses a new identity and preserves the damaged historical file. Use
`assets status PROJECT` for the remaining queue.

### Revising a rejected candidate

Do not overwrite an earlier successful response or re-run an identical source-only packet. Create
a new correction packet with concrete review feedback:

```text
littrans assets packet PROJECT --asset-ids ID1,ID2 --revision-notes "Correct the independently reviewed delimiter and equation-separation defects."
```

The revision input binds to the previous candidate and review fingerprints. Give it to a fresh
transcriber with the original images and its recorded revision notes, submit its new response,
then request a new independent asset audit. Revision notes identify a defect; they are not an
authoritative answer and never replace viewing the source. An old cached response remains
reusable as historical evidence, but replaying it must not roll the active candidate back to an
older version. Until the correction is verified and rendered, reading retains the original image.

A new transcription candidate cannot clear an independent reviewer's semantic uncertainty. The
candidate carries that finding through further revisions, including direct asset packet
submission; only a valid new review decision can supersede it, and damaged review evidence
restores the pending block. Older recovery candidates read the finding from their immutable
revision context. A rejected/unresolved asset with semantic uncertainty remains pending recovery:
workflow transcription prioritizes its revision packet with the prior review feedback, while
ordinary image fallback without uncertainty remains a complete reading representation.

Damaged candidate records require fresh transcription and independent review; recovered packets
use a new identity and include only blocking assets. Interrupted candidate/review publication can
repair an invalid index mapping on replay, but cannot displace newer valid evidence.

## Deterministic QA

`qa run` checks the batch's editable units and its whole dependency closure. Its cached result is
bound to the translation fingerprint and a QA context that includes approved terminology, asset
semantic uncertainty, recorded translation uncertainties, dependency presence, dependency source
and translation hashes, each scoped translation's viewing receipt and the current required-image
hashes. Any change there makes the cached report stale; QA context versions change when a rule
changes, and existing reports must then rerun.

Rules worth knowing when reading a report:

- A source unit with prose outside its `{{asset:ID}}` placeholders needs target text; a target
  that consists only of placeholders is an `empty-translation`, while a source that is only an
  asset reference (a whole figure or table block) needs none.
- Real footnote calls are counted with multiplicity in prose and table cells; literal
  code/math/escaped syntax (including tilde and backtick fences and escaped dollar delimiters) is
  excluded. Live calls inside image-language companions are rejected.
- Dollar inline math requires non-whitespace inner boundaries and a closing dollar not followed
  by a digit and cannot span intervening unescaped dollars, so ordinary currency amounts retain
  their real footnote calls; explicit double-dollar display math remains protected. Code fences
  start on a line with at most three leading spaces and close only on an otherwise blank line
  with a matching or longer delimiter; unclosed blocks extend to end of input.
- Translatable dependency units, including read-only context, need current translations with
  current receipts (`missing-translation`, `source-hash-mismatch`, `asset-image-receipt-missing`).
- Asset uncertainty across the dependency scope, including non-translatable formulas, blocks
  approval (`asset-semantic-uncertainty`); damaged images remain routable to source repair.
- Structured target tables render once, including when `target_text` is explicitly empty.

### Approved terminology

`glossary/approved.yaml` holds a `terms` list. Each entry has `source`, `target`, and optionally
`scope` (`document`, `page:N` or a parent unit ID), `status`, `match` and `forbidden`.

- Only entries whose `status` is absent or `approved` are enforced or injected into packets;
  `proposed`, `reference-only` and any other status are inert even inside `approved.yaml`.
  `glossary/candidates.yaml` is never enforced; it is listed in the finalize unresolved report.
- The unit's source representations (text, Markdown, table cells, figure labels) minus quoted
  titles are folded before matching, and so is `source`: precomposed, combining and TeX spacing
  accents (`Hölder` ≡ `H¨older`, `Lévy` ≡ `L´evy`), ligatures, curly quotes and apostrophes
  (`Chebyshev's` ≡ `Chebyshev’s`), dash variants, whitespace runs and case. QA and the
  `relevant_terms` packet injection share this folding, so a term shown to the translator is the
  term QA enforces.
- `match` selects how `source` is located in the folded text: `substring` (default; `measure`
  also hits `measurable`), `word` (no letter/digit on either side), or `regex` (a Python pattern
  searched case-insensitively in the folded text, e.g. `\bpartition\b(?! function)`). Invalid
  modes or patterns fail loading.
- When `source` occurs in a unit, `target` must appear in that unit's translation
  (`approved-term-missing`). A `source` that matches no prepared unit at all is reported once per
  QA run as the warning `approved-term-never-matched`; fix the spelling or narrow the entry.
- `forbidden` wording is checked in **every** translated unit and asset companion, whether or
  not that unit contains `source`. List only wording that is wrong in every context (a wrong
  transliteration), never a rendering that is merely wrong for this term (`mean` → 意味着).
- Editing the glossary changes the QA context of every batch and the audit context of batches
  whose relevant terms change; finish the terminology baseline before the audit wave.

## Audit coverage

Audit coverage is bound to the brief, the style guide, the relevant approved terms and the
dependency-closure units of each run. `audit_coverage` (and `workflow status`, `review status`)
reports why a run no longer counts: `context-changed`, `dependency-changed`, `unit-changed`,
`invalidated`, `closure-incomplete` or `context-units-removed`. Finish context edits before the
audit wave.

`review import-set` rewrites reviewer issue ids to canonical `audit-<hash>` ids and keeps the
reviewer id as `source_issue_id`; `review resolve` accepts either. Review issues against
read-only context route to an editable owning batch; issues against non-translatable source units
dispatch source review.

## Workflow coordination

`workflow packet` stages are `source-review`, `translate`, `revise`, `audit`, `transcribe` and
`asset-audit`. Use the emitted schemas as the authority for exact fields. All model work reads
original image evidence; a translate packet does not consume unverified transcription candidates.
New workflow packets record host/model/reasoning_effort in their manifest and identity. Legacy
manifests remain readable with absent policy fields; create fresh packets for an explicitly bound
dispatch policy. `workflow status --host` uses the same override as next/packet.

Workflow coordination rechecks source authority across each selected batch's page-evidence
closure before reusing QA. Failed authority returns `source-review`, suppresses optional asset
tasks and permits `workflow packet --stage source-review` to rebuild review materials; the returned
materials include open `workflow_issues`, which must be addressed and resolved in their original
batches. Repair missing/changed source artifacts before independent review; cached translation
approval cannot override this gate.

A `revise` packet (`workflow packet --stage revise`) carries the translate packet files plus the
batch's current translation records, its open review issues and `<batch>.revise.md`. Revision
packets retain their originating batch IDs for coordinator issue resolution. A dependency-only
QA failure dispatches its editable owning batch as prerequisite work, even outside resume bounds;
`requested_batch_ids` retains the original selected wave in `workflow next`. Uncertain fallback
assets dispatch transcription before QA regardless of whether their owning source unit is
translatable, and workflow dispatches the independent asset audit before QA.

A batch set for a packet or a render may span batch series when units do not overlap and source
order holds; within one series the batches must stay consecutive.

## Rendering

A project without any transcription candidate renders originals-only automatically; render QA
records `originals_only_reason: no-transcription-candidates`. For an explicitly all-original
reading edition of a project with candidates, render with `--originals-only`
(`originals_only_reason: requested`). Either way original-image representation is forced in both
Markdown and bilingual HTML without changing any candidate or review, and the candidate MathJax
bootstrap is omitted. The edition header, `*.quality.md` and `render-qa.json` (`rendered_status`)
reflect the lowest record status among the rendered units, not the project-wide status. Formal
dependency cover selection considers only current QA/audit/external evidence.

Reading output appends image-language companions after a complete continuation chain; footnote
companions remain inside their Markdown definitions. Markdown footnote calls and definitions use
unique labels derived from the referenced source unit ID, so repeated numbers on different pages
do not collide; definitions are indented after asset/companion expansion, and continued table
fragments with independent footnote scopes remain separate. Continued-table rendering includes
companions from every fragment, using each fragment's original source context for labels and
footnote scope. Code/math literals remain literal, including dollar and backslash inline/display
delimiters and multiline backtick/tilde fences.

Edition publication snapshots all shared MathJax files, including absent incoming paths, so a
later failure restores prior bytes and removes newly created runtime files; individual runtime
copies are atomic. Pydantic >=2.12 is required for conditional identity-field serialization.

## Resume and recovery

Persist successful responses before parsing or import. A cached response is reusable only for the
same input, model, prompt and relevant output fingerprints. Recover serialization failures
offline; do not pay for an identical successful response again. Imports are idempotent, but
changed source or packet hashes require fresh evidence. Record actual host/model metadata and
measured usage; token or monetary costs unavailable from the host stay unknown.

After interruption, ask status for the frozen batch IDs, recover unimported successful responses,
then dispatch only missing stages. Preserve unresolved original-image assets after translation
rendering; this is a recoverable checkpoint, not a failed reading artifact.
