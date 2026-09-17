# Fidelity workflow and recovery

Schema 6 uses one source preparation path. Native prose and layout regions produce immutable
source-owned `{{asset:ID}}` references; original vector SVG and high-resolution PNG evidence
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
13. [Project record and scaffold](#project-record-and-scaffold)
14. [Resume and recovery](#resume-and-recovery)

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
SHA-256, Python identity and installed distribution versions as well as the image contents and
weights — never a path of the project. A result under `derived/fidelity-layout/` keys its
`pages` by page-image SHA-256 (`images` maps the paths of the detection run to those keys), so
the same tree detects, finds and replays the same result under any root: a worktree, a clone
or a restored backup keeps its layout evidence without editing the cache. The store is
content-addressed: each result lives in `<fingerprint>.json` (with its `.request.json` and
`.log`), so a rerun on the same runtime reuses its file, a rerun on another runtime (an
upgraded detector or worker) writes a new file beside it, and a result some page ledger
records is never overwritten — a whole-chapter `--replace` after an upgrade re-detects the
pages without an override while every override page still replays on its recorded result.
Results written by earlier builds were named by source and page set and keyed pages by
absolute image path; they are still found by fingerprint and read by that path or, once the
tree has moved, by the page image's file name. An unsuccessful runtime metadata probe
cannot reuse cached layout evidence. Worker results are published atomically; unreadable or
incomplete cached JSON triggers recomputation, and malformed worker results (a page without a
list-valued prediction) report unavailable rather than being accepted.

The recorded result is a correctness input of every override replay, not a performance cache:
the page ledger names it (`layout_fingerprint`, `page_image_sha256`) and a replay cuts the
page with exactly that result. When the ledger records a result that the directory no longer
holds, the reason names the missing fingerprint and the two paths differ deliberately: an
override import stops with an error (re-cutting the reviewed page by the fallback rules would
silently change what the reviewer approved), while `source prepare --replace` replays the
override on the fresh detection of that run and lists the page in
`redetected_override_pages` — review it again from a new packet.

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
label that shares its PDF block with the tombstone closing a proof (`□ (1.50)`) binds too; the
tombstone stays in the reading order and structure assembly attaches it to the paragraph the
proof ends in. A label that stays in prose belongs to a block that preparation could not bind
to a display asset; structure assembly never merges such a label into the preceding paragraph.

A displayed block whose lines carry native prose (a cases formula with condition words) keeps
its rows: `source_text` separates them with `\n`, the Markdown edition emits hard breaks and the
HTML editions stack them as `display-row` spans; when the first row opens with an asset
placeholder followed by text (a stretched brace, an `X = {` head) that asset becomes the
`display-lead` column beside the rows. Translators keep one target row per source row
(`display-rows-mismatch` warning otherwise). Inline fragments are coalesced along a row only.

A printed list label opening a line is a structure boundary: a closed number (`1.`, `1.11.`,
`3.2.1.`, `2)`) or a bracketed clause marker (`(a)`, `(iv)`, `(2)`) set in a text face and
followed by text on the same line. A bare `1.1`, a four-digit year, a number alone on a wrapped
line (`1.32.`), a label in a mathematical face, a heading inside a title box, a display line and
running material are not labels. Preparation places a label geometrically before cutting at it —
it opens its PDF block, follows another label, opens right of the running text or of the open
item's label (a nested clause), or its text column is an open item's (a sibling returning to the
label column; hanging numbers such as `1.9.`/`1.10.` share a text column, not an x) — so a
sentence wrapping onto `2.3. The …` at the text column stays prose. Lines aligned with an item's
text column are that item's continuation and are never cut as indented paragraphs, even on a
page whose most common line start is the item column itself; prose resuming at an outer item's
column after a nested list starts its own chunk. The ledger's `structure.list_items` records
`{"label", "body_x"}` for each label chunk and `{"continues": <chunk>}` for each continuation
chunk; the key is absent on pages without labels. Bullet items keep their own rule (a bullet
always hangs, so prose returning left of the bullet column ends the item).

An `equation` unit without asset placeholders whose text shows no notation (an upright `Prob`
operator, a lone `otherwise.`) is native text: packets, Markdown and HTML render it — and its
translation — as text, not as a symbol sequence. Text with LaTeX commands, relations, digits or
Greek/operator characters, or a unit with `latex`, still renders as display math.

Glyphs of a mathematical face (CMMI, CMSY, CMEX, MSAM/MSBM, STIX, ...) that decode to control
characters are ink: CMEX encodes the integral sign as CR and big parentheses as LF, and a
display region owns them like any other glyph. A stretched delimiter assembled from pieces on
several baselines (⎧ ⎪ ⎨ ⎪ ⎩) is kept in one region; regions that had split it are merged and
record `stretched-delimiter-merged`. The review packet's `boundary_diagnostics` report
`math-ink-outside-ownership` when a symbol-face glyph inside a displayed crop is owned by
another asset, since the explicit export draws owned paths only and would leave a hole.

Words that stay inside a math crop, displayed or inline (`if`, `otherwise.`, `for all`,
`is even`, `i.o.`, `a.s.`), are declared automatically as `formula_conditions` (provenance
`auto-formula-conditions`), one per notation-free segment of a visual line, trimmed of
surrounding brackets and punctuation; word gaps TeX sets without a space glyph appear as spaces
in `source_text`. A language token is a word of two or more letters or a letter-dot
abbreviation (`i.o.`, `a.s.`, `i.e.`); the same predicate counts recoverable prose and
validates declared conditions. An operator name applied to its argument is notation, not a
condition: a known name (`limsup`, `Var`, `vol`), a capitalised name (`Prob(`) or any name set
flush against its argument's opening bracket (`mean(`, `area(`), whereas `if (` keeps its
text-mode space and remains a condition. The declared words make the unit translatable and
require an `asset_translations` companion, exactly as reviewer-declared conditions do. A
reviewer's region that names its glyphs or box but says nothing about `formula_conditions` is
declared the same way; an explicit list (even empty) is the reviewer's decision, and the
approval gate then reports language that list leaves undeclared.

A displayed formula box owns every row a stretched delimiter it owns brackets (the cases of
`ρ(x) = {…`), including a row that is mostly a condition word (`0, otherwise.`), and a phrase is
returned to the paragraph as set-off prose only when it sits beside the whole formula, not when
it lies within the horizontal extent of the formula's other rows (a fraction denominator such as
`vol(B)`).

Inline notation is collected per native line, and a text-face operator name set flush against
its argument's bracket (`Cov(`, `mean(`, `area(`) joins the run like a single letter or a known
operator does. A closing bracket in the text face is trimmed from the run's end only when the
run's closers outnumber its openers and prose follows it (`(the space L^p(Ω))`); intervals
count every bracket kind together, and a bracket at a line edge is never trimmed, since it may
belong to an expression continuing on the next line.

TeX breaks an inline formula only after a relation or operator. A native run that closes its
line with one (`f(λ) >`, a summation sign) continues in the run that opens the next line, even
when that run starts with a digit or bracket (`0`), and the two halves become one asset with
one fragment per line (provenance `line-break-continued`). A half that a display region
absorbed stays where it is; text-adjacent placeholders are still coalesced afterwards.

Original glyph paths are measured from the page SVG to size assets, including pages MuPDF
wraps in a page-sized clip group (CropBox differs from MediaBox). Glyphs that still cannot be
measured keep their font metric box and the owning region records `ink-bounds-unmeasured` in
its provenance, so a crop that truncates a stretched delimiter is traceable rather than silent.

A zero-width combining mark that the text layer attaches to the *previous* glyph (TeX's
negation slash U+0338, which MuPDF never advances the pen for) is folded into the relation it
negates: `∈` + U+0338 becomes one `∉` glyph at the relation's origin, where the page draws both
paths; `=`/`→` give `≠`/`↛`, and a pair without a precomposed form keeps the combining
sequence. The composite owns the ink of every path drawn at that origin, so the region exports
precisely instead of falling back to a raw `mixed-region` crop.

Words hyphenated across a line end are rejoined only when the rest of the document does not
print that compound more often than the joined word: `well-` / `known` stays `well-known` in
a book that prints `well-known` mid-line, while `proba-` / `bility` becomes `probability`.
A suspended hyphen inside a line (`pre- and post-processing`) is never altered.

Source authority transactions snapshot the unit and asset registries, translations, page
canvases, ledgers and receipts, and restore them on any error or user interruption. Source page
canvases are atomically published and included in that rollback. Incomplete original asset
caches are regenerated when their evidence receipt is absent, and a receipt whose file set is
not the current one (`original.svg` + `original.png`) is stale: the crop is re-exported and the
extra files are removed.

### Asset identity

An asset carries three related hashes; a consumer that recomputes any of them must follow
these rules (all hashes are SHA-256 of compact JSON with sorted keys, `_hash` in
`fidelity.py`):

- **Export identity** — `identity` in the crop directory's `evidence.json`:
  `{source_sha256, page, bbox, glyph_ids}` plus `export_method` unless the fragment is a raw
  region crop. The crop files are exported once per export identity and never rewritten.
- **Directory name and default asset ID** — fixed when the asset is created:
  `hash(identity)`, folded once more as `hash({"original_content": <that>,
  "formula_conditions": [...]})` when the creating region declared conditions (an asset with
  several fragments hashes the list of its fragments' values first). The default ID is
  `a-p<page>-<first 12 hex digits>`; a region `id` replaces the ID but not the directory. The
  directory name is therefore not derivable from `content_sha256`, and a declared condition
  changes both the name and the default ID of a *newly created* asset.
- **`content_sha256`** — the live identity that units and page fingerprints bind to:
  the fragment values above, folded with the current `formula_conditions`, then (content
  identity version 2) with `kind`, `display` and `grouping_pending`.

A preserved asset (`preserve_asset_id`) keeps the ID and directory it was created with: a
later change of `formula_conditions`, `kind`, `display` or `grouping_pending` moves only
`content_sha256`. Units and page ledgers reference the ID, so freezing it is what lets a
correction survive; a checker that recomputes directory names must therefore accept, for
every asset a page ledger's `source_overrides.regions[].preserve_asset_id` names, the
creation-time value (the identity hash without conditions declared after creation). No other
asset can have a directory name that differs from the rule above.

Crop directories under `derived/assets/fidelity/<hash>/` are addressed by export identity, so a
changed geometry writes a new directory. Once `source prepare --replace` or an override import
has committed the new registry it removes every directory no current fragment refers to
(`pruned_asset_directories` in the result); `source gc --dry-run` lists such orphans in an
existing project and `source gc --apply` removes them. Live directories are those named by
fragment `png_path`/`svg_path`, never by `content_sha256`. Copies under `output/original-assets/`
are not reclaimed.

`derived/provenance.json`, every page ledger and every source review packet carry a `generator`
block (`plugin_version`, `build_digest` of the package sources, `generated_at`), so an artifact
names the build that wrote it. The block is never fingerprinted: the page ledger's
`fingerprint`, the packet page fingerprint receipts bind to and the packet identity are
computed without it, so re-preparing identical content keeps the page fingerprint, the packet
ID (an existing valid packet is returned untouched rather than rewritten) and the review
receipt. `source prepare --replace` reports such pages in `retained_receipt_pages`; a page
whose fingerprint moved, or a page outside the run whose receipt depends on a re-prepared
page (continuation or container closure), loses its receipt explicitly and is listed in
`invalidated_pages`. `littrans doctor` prints the installed `build` (`plugin_version`,
`build_digest`, `package_path`) for comparison with an artifact's `generator`.

### Replaying reviewer overrides

A page ledger records the reviewer's `source_overrides` and, since it was imported, their
`source_overrides_origin` (`packet_id`, `reviewer`). `source prepare --replace` on such a page
replays the recorded override: the human decision is reproduced exactly (the recorded detector
result is reused; a page whose recorded result is gone is replayed on the fresh detection and
also listed in `redetected_override_pages`), never silently replaced by a fresh derivation. The
result lists these pages in `replayed_override_pages`. Pass `--discard-overrides` to re-derive
them from the current extraction rules instead (`discarded_override_pages`); a replay that no
longer applies (a pinned asset gone, glyph IDs changed) fails the whole transaction with the
page number and that hint, so re-import the review file or discard deliberately.

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
conditions. Legacy receipts without these bindings require a fresh visual review import; no
automatic approval migration is performed.

The packet directory a receipt names (`packets/source-<hash>/packet.json`, `coverage.html`
and the page images its manifest lists) is a live dependency of that review however many newer
packets exist: keep it on disk and under version control for as long as the receipt is meant to
verify. `source verify` lists the packets its verified receipts depend on in `receipt_packets`,
`source gc` reports `live_source_packets` and `unreferenced_source_packets` without deleting
either (a review file not yet imported may name an unreferenced one), and a receipt whose
packet is missing fails verification with a message naming the packet as a review dependency.

### Decision fields

Each entry of `pages` in the submitted review carries the packet page's `page` and `fingerprint`
plus the attestation flags from the template: `viewed_original`, `coverage_complete`,
`boundaries_complete`, `reading_order_correct`, `grouping_checked`, and, when the ledger requires
them, `layout_fallback_checked` (layout not `ok`), `overflow_canvas_checked` (a page canvas
override) and `formula_conditions_checked` (declared formula conditions: the reviewer confirms
the listed declarations are correct and complete, not that words are absent from the crop).
The template's `context` block lists what the ledger already knows for that page —
`formula_conditions` (asset, text, box), `grouping_pending` asset IDs, `boundary_diagnostics`
and `findings` — so the review confirms a list instead of guessing from crops.

The approval gate and the checkpoint's attention list share one predicate
(`page_review_findings`): an approved page is a page that needs no attention. A page passes
only when every required flag is `true`, `issues` is empty, no `override` is present and no
finding remains: `grouping-pending` (an asset with `grouping_pending` that the decision does
not list in `accepted_grouping_pending: [{"asset_id", "reason"}]` with a non-empty reason),
`undeclared-formula-language` (language inside a `math` crop that no formula condition
declares) and `recoverable-prose-in-image` (a `math`/`mixed-region` asset owning six or more
undeclared words). A receipt that does not pass records the reasons in `failures`, and the
import result lists them in `rejected_pages`. A `mixed-region` image is never textual coverage
of recoverable paragraphs: split the source region and obtain a fresh packet.

Overrides are applied before approvals. Decisions whose dependency fingerprints changed because
another decision in the same import corrected a page appear in `deferred_pages`; corrected pages
appear in `changed_pages`; pages outside the review whose receipt depended on a corrected page
(continuation or container closure) lose that receipt and appear in `invalidated_pages`. The
three lists are disjoint and together are what needs a new packet and review, never an
immediately stale receipt.

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
  `provenance` and `formula_conditions` (any `math` asset, inline or displayed; a `math` region
  that omits the key is declared automatically, an explicit list is kept as written).
  `preserve_asset_id` reuses an unchanged existing asset of the same page and may only change
  `kind`, `display`, `grouping_pending` or `formula_conditions`. A preserved `math` asset that
  carries no declaration is declared automatically from its own glyphs, exactly as a region
  naming the same glyphs would be (provenance gains `auto-formula-conditions`); an existing
  declaration is kept and an explicit list, even an empty one, is the reviewer's decision. A
  preserved asset keeps its ID and crop directory whatever the change (see
  [Asset identity](#asset-identity)). A region may not import `latex`. Glyph ownership may not
  overlap between assets, and asset IDs may not collide across decisions or with assets of
  another page.
- `units` replaces the page's source units. Each unit needs `unit_id`
  (`[A-Za-z0-9][A-Za-z0-9._-]*`, unique across the project), `source_markdown` (prose with
  `{{asset:ID}}` placeholders and `[^n]` footnote calls) and `bbox`; optional `kind` (default
  `paragraph`; `footnote`, `heading`, `list_item`, `caption`, `equation`, `figure`, `table`,
  `note`, `bibliography`), `equation_number`, `footnote_number`, `footnote_refs`, `parent_id`,
  `continues_from_previous`, `continued_to_next`, `render_policy` (`include`/`omit`) and
  `translatable`. Across the page's units every page asset must be referenced exactly once. When
  `units` is omitted the units are re-derived from the regions with the normal structure
  assembly and inline-fragment coalescing.
- A region `bbox` (or fragment `bbox`) is the target box: only owned glyph ink is padded by
  0.5pt, so a `fragment.bbox` copied from the packet reproduces the same fragment, `width`,
  `height`, `baseline` and `content_sha256`. Fragment dimensions derive from the 4-decimal
  `bbox`, so a re-derived fragment compares byte for byte.
- Footnote relationships are validated against the retained and replacement units together:
  unknown, non-footnote or duplicate targets and call numbers that do not match the referenced
  definitions reject the whole import.
- Same-page boundary repairs may retain stable IDs; changed content fingerprints invalidate old
  evidence and require fresh source review. Translations of removed unit IDs are retired to
  `translations/source-retired.jsonl` and removed from the current ledger in the same rollback
  transaction; batches containing changed units receive audit invalidations.

### Formula-contained language and original page overflow

A complete displayed cases formula may contain condition words such as “and … is odd”, and an
inline crop may hold an abbreviation such as `i.o.` or `a.s.`. Preparation declares them itself
(see [Prepared units and assets](#prepared-units-and-assets)); a reviewed region can also
declare `formula_conditions: [{glyph_ids: [...], source_text: "..."}]` on any `math` asset.
Each entry must match owned native glyphs in native order on one visual line; `source_text`
is compared ignoring whitespace, so TeX word gaps may be written as spaces. The source gate
still checks all undeclared prose and reports language the declarations miss as the
`undeclared-formula-language` finding. It additionally requires the independent page review's
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
substitution or LaTeX inference. A region the exporter cannot isolate keeps its raw-region SVG/PNG crop.
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
inferred statement. A numbered label unit (`1.11. Let …`, an exercise or numbered item) opens a
parent group of its own unless a statement is open, in which case it joins the statement like a
bracketed clause; bracketed clauses (`(a)`, `(ii)`) join the paragraph or statement that
introduces them, and the item's displays and continuation chunks join its group. A label unit
is never merged into the unit before it; a word hyphenated across the seam of two merged
fragments is rejoined by the same document evidence as a line end inside a block
(`well-`/`known` stays `well-known`). Source review must confirm ambiguous and cross-page boundaries. Children
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
copied MathJax runtime and original SVG/PNG files (fragments prepared before 0.6.1 may also carry a
per-region `original.pdf`; it is no longer written, linked or copied, and `source prepare --replace`
removes it from the directories it re-exports). Review submissions must echo both
`render_artifact_sha256` and `render_manifest_sha256` from the packet after inspecting the actual
artifact. Imports and subsequent status queries verify all dependencies. Old packets without this
manifest require a new audit and cannot retain verified status; their candidates and history
remain available. Rebuilding a damaged render creates a new packet identity and requires a fresh
review, never silently repairs an old approval.

### Submission and review

Submit transcription through `assets submit PROJECT INPUT`; the envelope includes `packet_id`,
`author_task_id`, the packet's dispatch `model` and `reasoning_effort` echoed verbatim, `image_evidence`,
`candidates` and available `usage` (otherwise `null`); an optional `served_model_label` records the model the
host environment reported under that dispatch value (stored as is, unverified, never gated). Candidates name `asset_id`, `format` and `content`; `status` is
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

### Terminology

Three glossary files share one entry schema — a `terms` list whose entries carry `source` and
optionally `aliases` (other attested source forms), `match`, `scope` (`document`, `page:N` or a
parent unit ID), `status`, `target`, `forbidden` and any project-defined key — and differ only
in effect:

| File | Effect | Reaches packets | In the audit hash |
| --- | --- | --- | --- |
| `approved.yaml`, `status` absent or `approved` | hard per-unit QA gate | entries matching the packet's units | those entries |
| `approved.yaml`, `status: reference-only` | binding, never gated | same filter | same |
| `reference.yaml` (`status` defaults to `reference-only`) | binding, never gated; grouped by `kind` | same filter | same |
| `status: proposed` in either file | inert | no | no |
| `candidates.yaml` | none: the record of promotion decisions | no | no |

- Reference entries are the channel for data that grows with the chapters but must not gate:
  proper names kept in source form, one-word-two-senses registers, chapter usage notes.
  `kind` (default `reference`, e.g. `proper-name`, `sense`) groups them in packets; every other
  key (`targets`, `rule`, `note`, `first_seen`, ...) is shown verbatim. `status: approved` inside
  `reference.yaml` is refused: that file never gates. Because reference entries are filtered per
  unit like approved terms, appending a chapter's names changes only the audit context of the
  batches that mention them, and correcting an entry resets only the batches it matches — the
  two context files, by contrast, are hashed whole.
- Packets show the gated entries under `# Relevant approved terminology` (`approved_terms`)
  and, only when at least one matches, the reference entries under
  `# Relevant reference terminology (not gated)` (`reference_terms`, one list per `kind`); the
  batch `context.md` and external-review packets carry the same two sections. A project without
  reference entries keeps the audit context it had before the channel existed.
- `candidates.yaml` entries without `status` (or `status: proposed`) are undecided and are
  listed in the finalize unresolved report; entries whose status records a decision
  (`reference-only`, `rejected`, ...) are only counted there.
- `glossary lookup PROJECT --batch-id ID | --pages SPEC | --unit-ids IDS | --text FILE`
  lists the approved and reference entries a selection receives, with the packet's own scope
  and folding rules (`--kind` narrows the reference groups, `--jsonl` emits one entry per
  line); `glossary check PROJECT` loads every file and reports entries matching no prepared
  unit. Both are read-only.
- The unit's source representations (text, Markdown, table cells, figure labels) minus quoted
  titles are folded before matching, and so is `source`: precomposed, combining and TeX spacing
  accents (`Hölder` ≡ `H¨older`, `Lévy` ≡ `L´evy`), ligatures, curly quotes and apostrophes
  (`Chebyshev's` ≡ `Chebyshev’s`), dash variants, whitespace runs and case. QA and the
  `relevant_terms` packet injection share this folding, so a term shown to the translator is the
  term QA enforces.
- `match` selects how `source` is located in the folded text: `substring` (default; `measure`
  also hits `measurable`), `word` (no letter/digit on either side), or `regex` (a Python pattern
  searched case-insensitively in the folded text, e.g. `\bpartition\b(?! function)`). The
  literal characters of a regex are folded like a substring source (`Hölder`, `Chebyshev’s`
  and `H¨older`, `Chebyshev's` are the same pattern) while escape sequences such as `\b`, `\B`
  or `\s` are kept verbatim. Invalid modes or patterns fail loading.
- When `source` occurs in a unit, `target` must appear in that unit's translation
  (`approved-term-missing`). A `source` that matches no prepared unit at all is reported once per
  QA run as the warning `approved-term-never-matched`; fix the spelling or narrow the entry.
- `forbidden` wording is checked in **every** translated unit and asset companion, whether or
  not that unit contains `source`. List only wording that is wrong in every context (a wrong
  transliteration), never a rendering that is merely wrong for this term (`mean` → 意味着).
- Editing a gated entry changes the QA context of every batch and the audit context of batches
  whose relevant terms change (existing audits become `audit_stale`); finish the gate baseline
  before `source prepare`, or at the latest before the audit wave. Drafts belong in
  `glossary/candidates.yaml`, which has no effect until an entry is moved into `approved.yaml`
  or `reference.yaml`.

## Audit coverage

Audit coverage is bound to the brief, the style guide, the relevant approved and reference
terms and the dependency-closure units of each run (`audit_context_text` is exactly those four
parts; `project.yaml` is not among them). `audit_coverage` (and `workflow status`,
`review status`) reports why a run no longer counts: `context-changed`, `dependency-changed`,
`unit-changed`, `invalidated`, `closure-incomplete` or `context-units-removed`. A
`context-changed` run recorded after this version also lists `context_changes` — which part
(`document-brief`, `style-guide`, `approved-terms`, `reference-terms`) changed and its line
count before and after — so the cost of growing a whole-file context is visible where it is
paid. Finish context edits before the audit wave.

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

## Project record and scaffold

`project init` creates the schema-6 directories and grows the record structure a project needs
before its first page is prepared; `project scaffold PROJECT` adds whatever is missing to an
existing project and `--refresh` regenerates the plugin-owned file after an upgrade. User-owned
files are written once and never overwritten, so both calls are idempotent on a project in
progress. `--repo-root DIR` places the repository-level files at an ancestor of the project root
for nested layouts (`repo/workspace`); the default is the project root.

| File | Owner | Purpose |
| --- | --- | --- |
| `context/document-brief.md`, `context/style-guide.md` | user | rules only; each opens with the boundary note (whole file hashed → records go to `glossary/reference.yaml`) |
| `glossary/approved.yaml`, `candidates.yaml`, `reference.yaml` | user | the three terminology stores, each headed by its effect |
| `.gitignore` (project root) | user | keeps the source PDF, `output/`, the layout cache, per-asset `original.pdf` and unreferenced source packets out; keeps `.littrans/work/` in and the lock out; an existing file only gains the `.littrans/*` / `!.littrans/work/` pair |
| `.gitattributes`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `PLUGIN-ISSUES.md`, `docs/{HISTORY,DECISIONS,TERMINOLOGY,REVIEWS}.md` | user | LF policy, handbook, operating manual (`CLAUDE.md` imports `AGENTS.md`), defect ledger and the four records — headings plus one line each on what belongs there, nothing document-specific |
| `tools/lt.py`, `tools/lt.cmd`, `tools/lt.sh` | user | launcher: `LITTRANS_PLUGIN_ROOT`, else the recorded plugin root, else its highest-versioned sibling (numeric, pre-release aware) |
| `docs/LITTRANS.md` | **plugin** | what the installed build guarantees — version, build digest, term semantics, audit context parts, stale reasons, stages, wave limits, status order, QA version — rendered from the package constants; regenerated by `--refresh`, never hand-edited |

`project tracked PROJECT` derives the record from the data — `project.yaml`, context and
glossary files, `derived/units.jsonl`, `fidelity-assets.jsonl`, `provenance.json`, the page
ledgers, every asset crop and its `evidence.json`, review templates, the source packets page
receipts name (live dependencies), reviews, page evidence, audit ledgers, batch files,
translations, QA reports and `.littrans/work/` payloads — and the excluded set (source PDFs,
`output/*.html`, the layout cache, per-asset `original.pdf`, unreferenced packets,
`.littrans/state.json`), then asks git (`ls-files`, `check-ignore`) whether exactly that is
tracked. It reports a record file that is ignored or uncommitted, an excluded file that is
tracked, and any file in neither state; the exit code is 1 on any problem. `project rebuild`
copies `docs/` alongside `context/` and `glossary/` and refreshes `docs/LITTRANS.md`.

## Resume and recovery

Persist successful responses before parsing or import. A cached response is reusable only for the
same input, model, prompt and relevant output fingerprints. Recover serialization failures
offline; do not pay for an identical successful response again. Imports are idempotent, but
changed source or packet hashes require fresh evidence. Record actual host/model metadata and
measured usage; token or monetary costs unavailable from the host stay unknown.

After interruption, ask status for the frozen batch IDs, recover unimported successful responses,
then dispatch only missing stages. Preserve unresolved original-image assets after translation
rendering; this is a recoverable checkpoint, not a failed reading artifact.
