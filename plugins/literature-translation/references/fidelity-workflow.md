# Fidelity workflow and recovery

Schema 6 uses one source preparation path. Native prose and layout regions produce immutable source-owned `{{asset:ID}}` references; original vector PDF/SVG and high-resolution PNG evidence remain available throughout the workflow. A detector warning requires a source decision, not an automatic formula-recognition retry.

Workflow coordination rechecks source authority across each selected batch's page-evidence
closure before reusing QA. Failed authority returns `source-review`, suppresses optional
asset tasks and permits `workflow packet --stage source-review` to rebuild review materials.
Repair missing/changed source artifacts before independent review; cached translation approval
cannot override this gate. Review issues against read-only context route to an editable owner;
revision packets retain their originating batch IDs for coordinator issue resolution.
Issues against non-translatable source units also dispatch source review; returned source-review
materials include open `workflow_issues`, which must be addressed and resolved in their original batches.

Reading output appends image-language companions after a complete continuation chain.
Formal dependency cover selection considers only current QA/audit/external evidence.
Pydantic >=2.12 is required for conditional identity-field serialization; project schema remains 6.

New explicit-unit and untranslated-only batches record `frozen_scope: true`. Refresh keeps
their selected IDs rather than filling interior gaps; removed IDs or newly cut logical groups
require an explicit new selection. Legacy/range manifests retain interval refresh behavior.
Footnote companions remain inside their Markdown definitions. Edition publication snapshots
all shared MathJax files, including absent incoming paths, so later failure restores prior bytes
and removes newly created runtime files; individual runtime copies are atomic.

QA v6.12 shares code-fence recognition with Markdown and HTML: fences start on a line
with at most three leading spaces and close only on an otherwise blank line with a matching
or longer delimiter. Unclosed blocks extend to end of input; inline backticks match exact runs.
Existing QA must rerun. A managed installer without a previous READY marker redownloads
the model snapshot even when its config and main weight file already exist.

Managed layout components require a successful smoke-test READY marker both for status
checks and before detector execution or cached result reuse. Fully external interpreter
and model configurations retain their external-runtime exemption. Cache identities bind
the worker SHA-256, configured/resolved interpreter, interpreter SHA-256, Python identity
and installed distribution versions as well as images and weights. An unsuccessful
runtime metadata probe cannot reuse cached layout evidence.

Source-review receipts bind the decision, reviewer, source, page fingerprint and original
packet identity/hash with `receipt_sha256`. Approval consumers verify the receipt and
packet, then recheck the visual decision conditions. Keep the original packet available.
Coverage HTML and its referenced page images are bound by the packet `visual_report`
manifest. Submissions must echo `visual_report_sha256` after inspection, and receipts
retain it. Import and later approval reads verify these files. Relative image URLs keep
reports portable. A damaged report is rebuilt under a new packet identity and cannot
silently restore prior approval.

Source override asset and unit IDs cannot collide across decisions or with IDs owned by another page.
Override unit IDs use `[A-Za-z0-9][A-Za-z0-9._-]*`; legacy IDs are escaped when rendered.
Same-page boundary repairs may retain stable IDs; changed content fingerprints invalidate
old evidence and require fresh source review. `preserve_asset_id` reuses an unchanged
original crop on its own page.

Legacy receipts without these bindings require a fresh visual review import; no automatic
approval migration is performed. Project schema remains 6.

## Independent states

- Source fidelity: all selected content has reviewed ownership, reading order and complete boundaries, bound to the source fingerprint.
- Translation: submitted prose, deterministic QA, three independent audit lenses, configured external review and optional explicit human approval.
- Asset representation: original image, structured candidate, independent visual/render review, reliable candidate or original-image fallback.

The second and third states advance independently after source fidelity. Transcription is optional enhancement and may be initiated at any later time, including after the reading edition is complete. `workflow next` and `ready_tasks` schedule translation work; `optional_asset_tasks` exposes the independent enhancement queue. `complete` means the reading workflow is complete, while `assets_complete` separately reports enhancement progress. Missing LaTeX never blocks an otherwise reviewed translation. Missing understanding or source content does. Nonempty recorded translation `uncertainties` block QA for the affected unit and its semantic dependencies; resolve the actual understanding problem and resubmit before approval. Merely unfinished LaTeX belongs to asset progress and must not be recorded as an unresolved translation-understanding problem. Source/content/ownership changes invalidate affected translation dependencies; a display-only change invalidates rendering evidence rather than unrelated prose audits.

## Narrow original-glyph corrections

When a region includes neighboring prose, a source reviewer may explicitly name its existing PDF `glyph_ids`. The narrow glyph exporter copies the selected original PDF vector paths and supported nearby horizontal rules into the reading SVG and model PNG; it performs no OCR, character substitution or LaTeX inference. The separate original PDF fragment remains raw-region evidence. This correction requires inspected ownership and a new packet-bound source review; it is not an automatic formula recognizer.

Bounds include the actual visible vector ink, because font-reported boxes can put accents or italic overhangs outside their nominal rectangle. SVG dimensions, PNG export and baseline placement use the expanded bounds. Unmapped glyphs, unsupported SVG groups/transforms or vector primitives fail closed. Retain the previous original region, or issue a new complete raw-region correction without explicit glyph filtering, and review that fallback; never approve a silently incomplete filtered image.

Footnotes keep `footnote_number` on the footnote unit and `footnote_refs` on its calling unit. References contain stable source unit IDs, each resolving to a `footnote` unit, without duplicates. Preserve the number and relationship separately from prose. Source review and audit dependencies include both caller and footnote, including cross-page links; a changed footnote invalidates its dependent caller's evidence. HTML provides links to the original footnote unit anchors and displays its recorded number.

## Packets and imports

Structured candidate formats are bound to source kind: `math` accepts `latex`, `table`
accepts `table`, and `code` accepts `code`. Figures and `mixed-region` assets remain
original images; use a supported source review correction to establish a more precise
kind before requesting transcription. Free text is not a replacement format for these kinds.

Asset-audit packets bind `render_manifest` (relative render-directory paths to SHA-256)
and `render_manifest_sha256` into the packet identity. The manifest covers the comparison
HTML, copied MathJax runtime and original SVG/PNG/PDF files. Review submissions must echo
both `render_artifact_sha256` and `render_manifest_sha256` from the packet after inspecting
the actual artifact. The manifest receipt is a required 64-character lowercase SHA-256
string in the submission schema. Imports and subsequent status queries verify all dependencies.
Stored review payloads must match their `review_sha256` before decisions are consumed,
imports replayed or revision context built; corrupt evidence cannot grant verified status.
Old packets without this manifest require a new audit and cannot retain verified status;
their candidates and history remain available. Rebuilding a damaged render creates a
new packet identity and requires a fresh review, never silently repairs an old approval.

Use the emitted schemas as the authority for exact fields. `workflow packet` stages are `transcribe`, `translate`, `asset-audit` and `audit`. All model work reads original image evidence. A translate packet does not consume unverified transcription candidates.

Submit transcription through `assets submit PROJECT INPUT`; the envelope includes `packet_id`, `author_task_id`, actual `model`, `reasoning_effort`, `image_evidence`, `candidates` and available `usage` (otherwise `null`). Candidates name `asset_id`, `format` and `content`; `status` is `candidate` or `unresolved`, with `notes` and `semantic_uncertainty` as needed. LaTeX content is a math body without dollar delimiters; table content uses a rectangular `rows` array of cell strings. Both candidate and review envelopes record actual viewing in `image_evidence` using the packet's `required_images` path/hash map. An asset reviewer has a different `reviewer_task_id` and returns `render_artifact_sha256` and `render_manifest_sha256` plus one decision per asset, with `candidate_sha256`, `verdict` (`accept`, `reject`, or `unresolved`), `visual_checked` and `render_checked`; import using `assets import-review PROJECT INPUT --confirm-visual-review` only when those checks were performed. Use `assets status PROJECT` for the remaining queue.

Translation records retain their unit's `source_hash`, references and an `image_evidence` map of inspected original image path to SHA-256 from `original-images.json`. Supplementary translations of image-contained language live in `asset_translations`, keyed by `asset_id`, using `target_text`, `target_table` or `figure_labels`. Declare `language_present: false` with explanatory `notes` only for an asset with no translatable natural language. Asset candidate content never replaces the source reference ID. Do not put raw LaTeX in translated image companions (`asset_translations`); preserve references and submit mathematical candidates through the independently reviewed asset channel.

## Resume

Persist successful responses before parsing or import. A cached response is reusable only for the same input, model, prompt and relevant output fingerprints. Recover serialization failures offline; do not pay for an identical successful response again. Imports are idempotent, but changed source or packet hashes require fresh evidence. Record actual host/model metadata and measured usage; token or monetary costs unavailable from the host stay unknown.

Audit coverage is bound to the brief, the style guide, the relevant approved terms and the dependency-closure units of each run; `audit_coverage` (and `workflow status`, `review status`) reports why a run no longer counts: `context-changed`, `dependency-changed`, `unit-changed`, `invalidated`, `closure-incomplete` or `context-units-removed`. Finish context edits before the audit wave.

A `revise` packet (`workflow packet --stage revise`) carries the translate packet files plus the batch's current translation records, its open review issues and `<batch>.revise.md`; a batch set may span batch series when units do not overlap and source order holds.

After interruption, ask status for the frozen batch IDs, recover unimported successful responses, then dispatch only missing stages. Preserve unresolved original-image assets after translation rendering; this is a recoverable checkpoint, not a failed reading artifact.


## Revising a rejected asset candidate

Do not overwrite an earlier successful response or re-run an identical source-only packet. Create a new correction packet with concrete review feedback:

```text
littrans assets packet PROJECT --asset-ids ID1,ID2 --revision-notes "Correct the independently reviewed delimiter and equation-separation defects."
```

The revision input binds to the previous candidate and review fingerprints. Give it to a fresh transcriber with the original images and its recorded revision notes, submit its new response, then request a new independent asset audit. Revision notes identify a defect; they are not an authoritative answer and never replace viewing the source. An old cached response remains reusable as historical evidence, but replaying it must not roll the active candidate back to an older version. Until the correction is verified and rendered, reading retains the original image.

## Logical statement containers

Reviewed `parent_id` groups retain a theorem, lemma, proposition, definition, corollary or claim together with enumerated clauses and display equations. Preparation recognizes explicit statement labels and enumerated children conservatively; a heading, proof or new indented prose ends the inferred statement. Source review must confirm ambiguous and cross-page boundaries. Children retain stable IDs, equation numbers and markup. Batching keeps a whole parent group together and bilingual rendering presents contiguous children in one row, preserving child anchors and paragraph/list boundaries.

Continuation dependencies skip omitted running material and footnotes and require physically adjacent PDF pages. A fragment at the boundary of a noncontiguous page selection remains a fragment; it must not be joined to the next extracted page across a gap. Parent groups also participate in source evidence and audit dependency closure, so changing one clause requires rechecking the whole statement.

A project without any transcription candidate renders originals-only automatically; render QA records `originals_only_reason: no-transcription-candidates`. For an explicitly all-original reading edition of a project with candidates, render with `--originals-only` (`originals_only_reason: requested`). Either way original-image representation is forced in both Markdown and bilingual HTML without changing any candidate or review, and the candidate MathJax bootstrap is omitted. The edition header, `*.quality.md` and `render-qa.json` (`rendered_status`) reflect the lowest record status among the rendered units, not the project-wide status.

## Formula-contained language and original page overflow

A complete displayed cases formula may contain condition words such as “and … is odd”. A reviewed region can declare `formula_conditions: [{glyph_ids: [...], source_text: "..."}]`. Each entry must match owned native glyphs in native order on one visual line. The source gate still checks all undeclared prose. It additionally requires the independent page review's `formula_conditions_checked`; the asset remains math, its source unit becomes translatable, and translation QA requires a Chinese companion and rejects a no-language attestation. Empty declarations are omitted from serialization to preserve existing source fingerprints.

An explicit page override `page_canvas_bbox: [0, 0, width, height]` may reveal preexisting content-stream glyphs outside an unrotated PDF page's current box. Only the temporary in-memory document is expanded. The original page image remains unchanged; the ledger adds a hashed overflow image and the original page box, and original-context packets require both images. Review must explicitly attest `overflow_canvas_checked`. Recovered glyphs participate in normal ownership, immutable image fragments and source verification. No OCR or replacement symbols are introduced.

## Document-specific preparation

Run `source probe PROJECT --pages PAGES` before a new scope's extraction. Complete the source-bound profile using [document-structure.md](document-structure.md). Source preparation records its hash in new page ledgers; review packets include the profile and reject imports after its contents change. Batch context includes the same document-specific handling rules. The generic extractor remains a proposal generator: agents apply document-specific decisions using supported source overrides, then inspect fresh coverage evidence. A profile does not establish source fidelity or silently re-extract already reviewed pages.

Container kinds are open-ended, including examples, exercises, cases, algorithms or book-specific forms. An enclosing container can include multiple author paragraphs, displays, lists and proofs. Keep those internal boundaries: `parent_id` expresses scope, not a command to concatenate every child. A paragraph around a display remains one logical scope; a following “where” clause must not become an unrelated batch. Continuation across pages requires source indentation and context, not punctuation alone.

Markdown footnote calls and definitions use unique labels derived from the referenced
source unit ID, so repeated numbers on different pages do not collide. Code/math
literals remain literal, including dollar and backslash inline/display delimiters. Definitions are indented after asset/companion expansion;
continued table fragments with independent footnote scopes remain separate.
# Review follow-up contracts

- Explicit footnote call numbers must match unique referenced definition numbers;
  literal escaped/code/math syntax is excluded. Inconsistent historical structures need
  source preparation/correction before approval.
- A rejected/unresolved asset with semantic uncertainty remains pending recovery.
  Workflow transcription prioritizes its revision packet with the prior review feedback;
  ordinary image fallback without uncertainty remains a complete reading representation.
- Source authority transactions restore snapshots on user interruption as well as errors.

- Batch output schemas come from the submission model and include image_evidence and
  asset_translations; refresh existing batches to update their emitted schema.
- New workflow packets record host/model/reasoning_effort in their manifest and identity.
  Legacy manifests remain readable with absent policy fields; create fresh packets for
  an explicitly bound dispatch policy.
- Source overrides validate footnote references against all retained and replacement
  units together. Unknown/non-footnote/duplicate targets reject the transaction.

- Newly prepared assets use content identity version 2, binding original fragments,
  formula conditions, kind, display and grouping state. Legacy version 1 remains
  readable; preparation or semantic overrides create version 2 identities and require
  current source/translation evidence. Project schema remains 6.
- Untranslated-only batch manifests retain complete selected parent groups. Their
  `read_only_unit_ids` are context, excluded from `translatable_unit_ids` and submission;
  refresh preserves that boundary. Packet source and context identify both scopes.
- `workflow status --host` uses the same override as next/packet. A dependency-only
  QA failure dispatches its editable owning batch as prerequisite work, even outside
  resume bounds; `requested_batch_ids` retains the original selected wave in next.
- QA context version 6.5 separates original-image companions from extracted-prose
  checks and counts table-cell footnote calls. Existing QA reports must be rerun.
# Recovery review authority

QA v6.11 also rejects live footnote calls in image-language companion text, table cells
and label source/target fields. Keep calls in the main translation; escape literal notation
or use code literals. Existing QA must rerun; project schema remains 6.

QA v6.10 binds dependency presence, source content and translation source hashes into its
cache identity. It requires current translations for all translatable dependency units, including
read-only context. Batch refresh reopens read-only units with missing/stale translations.
Source validation, QA and Markdown share the literal-aware footnote tokenizer, including
escaped dollar delimiters. Layout cache reuse requires the recorded page's list of results.

Source-review cache lookup skips unreadable entries and explicitly degrades when no valid
matching layout remains. Continued-table rendering includes companions from every fragment,
using each fragment's original source context for labels and footnote scope.

Layout-worker cache results are published atomically. Unreadable or incomplete cached
JSON triggers recomputation, and malformed worker results report unavailable rather than
being accepted. HTML literal handling includes multiline backtick and tilde fences.

QA v6.8 also protects tilde-fenced code when counting real footnote calls. Structured
target tables are rendered once, including when target_text is explicitly empty; source
table cells remain available in bilingual and source-review HTML.

Damaged candidate records require fresh transcription and independent review; recovered
packets use a new identity. Asset task scope includes semantic dependencies outside the
requested batch, matching QA. QA v6.7 excludes literal code/math/escaped footnote syntax
while preserving call multiplicity. Existing QA must rerun; project schema remains 6.

Source overrides retire translations for removed unit IDs to `translations/source-retired.jsonl`
and remove them from the current ledger in the same rollback transaction. Overrides are
applied before approvals; decisions with changed dependency fingerprints appear in
`deferred_pages` and require a new packet, never an immediately stale approval receipt.

An indexed review that cannot be validated blocks QA until renewed independent review.
Rebuilding its audit packet uses a new identity and preserves the damaged historical file.
Uncertain fallback assets dispatch transcription before QA regardless of whether their
owning source unit is translatable. QA v6.6 checks the entire dependency scope; existing
QA reports require rerunning. Project schema remains 6.

A new transcription candidate cannot clear an independent reviewer's semantic uncertainty.
The candidate carries that finding through further revisions, including direct asset packet
submission. Workflow dispatches its independent asset audit before QA. Only a valid new
review decision can supersede the finding; damaged review evidence restores the pending
block. Older recovery candidates read the finding from their immutable revision context.

Interrupted candidate/review publication can repair an invalid index mapping on replay,
but cannot displace newer valid evidence. Recovery packets include only blocking assets;
damaged candidates receive fresh transcription while valid rejected candidates retain their
revision context. Companion text, table cells and label mappings cannot contain asset placeholders.
Source page canvases are atomically published and included in source-authority rollback.
Both exact layout caches and worker responses require list-valued predictions for every page.
