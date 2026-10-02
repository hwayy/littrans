# Source Processing

For source receipts and asset boundaries, see [fidelity workflow](fidelity-workflow.md).

Source preparation preserves text, original assets and reading structure for independent
[source review](source-review.md). Packet fields and supported corrections are defined by the
[CLI contract](cli-reference.md#source-review-contract).

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
`pages` by page-image SHA-256 (`images` maps the page-image file names of the detection run to
those keys), so the same tree detects, finds and replays the same result under any root: a
worktree, a clone or a restored backup keeps its layout evidence. The store is
content-addressed: each result lives in `<fingerprint>.json` (with its `.request.json` and
`.log`), so a rerun on the same runtime reuses its file, a rerun on another runtime (an
upgraded detector or worker) writes a new file beside it, and a result some page ledger
records is never overwritten. Results written by earlier builds were named by source and page
set and keyed pages by absolute image path; they are still found by fingerprint and read by
that path or, once the tree has moved, by the page image's file name. An unsuccessful runtime
metadata probe cannot reuse cached layout evidence. Worker results are published atomically;
unreadable or incomplete cached JSON triggers recomputation, and malformed worker results (a
page without a list-valued prediction) report unavailable rather than being accepted.

The recorded result is a correctness input of every rerun, not a performance cache: the page
ledger names it (`layout_fingerprint`, `page_image_sha256`), no other detector runtime
reproduces its fingerprint, and the fingerprint also binds the set of pages detected together,
so even the same runtime would give `--replace --pages 30` a new fingerprint. Result files
(`<fingerprint>.json`) are therefore part of the record and travel with it (the generated
`.gitignore` keeps only the run's `.request.json` and `.log` out, which carry the host's paths
and interpreter); `project tracked` reports a result that is not committed. `source extract
--replace` cuts every page whose ledger records a result the directory still holds on that
result — override page or not — and calls the detector only for the pages without one, so a
rerun on another build or host reproduces the recorded pages (and their receipts) without a
layout runtime: the result lists `reused_layout_pages` and `detected_layout_pages`, and
`layout_status` is `reused` when nothing was detected. `--redetect` runs the detector for
every page instead (a deliberately upgraded detector); a page prepared without a usable result
(`layout_status` not `ok`) is always detected.

When the ledger records a result that the directory no longer holds, the reason names the
missing fingerprint and the two paths differ deliberately: an override import stops with an
error (re-cutting the reviewed page by the fallback rules would silently change what the
reviewer approved), while `source extract --replace` replays the override on the fresh
detection of that run and, when that detection has another fingerprint, lists the page in
`redetected_override_pages` — review it again from a new packet.

### Document-specific preparation

Run `source probe PROJECT --pages PAGES` before a new scope's extraction. Complete the
source-bound profile using [document-structure.md](document-structure.md). Source preparation
records the digest of the guidance that applies to each page (`structure.document_profile.
guidance_sha256`: the base `handling_rules` plus the `page_rules` blocks covering the page)
in new page ledgers; review packets embed the profile, and a receipt or an import is refused
only when the guidance of that page changed since the packet was built — the message names the
page and the keys. Probing further pages, scoped blocks for other pages, notes, status and the
file's formatting change no page's guidance; editing a base rule changes every page's. Batch
context includes the base handling rules and the blocks covering the batch's pages. The
generic extractor remains a proposal generator: agents apply document-specific decisions using
the supported source overrides below, then inspect fresh coverage evidence. A profile does not
establish source fidelity or silently re-extract already reviewed pages.

Container kinds are open-ended, including examples, exercises, cases, algorithms or book-specific
forms. An enclosing container can include multiple author paragraphs, displays, lists and proofs.
Keep those internal boundaries: `parent_id` expresses scope, not a command to concatenate every
child. A paragraph around a display remains one logical scope; a following “where” clause must
not become an unrelated batch. Review indentation and context before recording cross-page
paragraph fragments; a shared container does not establish paragraph continuity.

### Prepared units and assets

Preparation splits a native block that contains a display formula into prose chunks and one
`equation` unit per display (`<block>-displaypartN` IDs); footnote links follow the chunk that
still carries the call, and an inconsistent footnote graph is rejected before anything is
published. Newly prepared assets use content identity version 2, binding original fragments,
formula conditions, kind, display and grouping state. Legacy version 1 remains readable;
preparation or semantic overrides create version 2 identities and require current
source/translation evidence.

Prose and titles use `paragraph` with the [recorded properties](paragraph-model.md).
Detector heading/list labels, font changes, spacing, line starts and PDF bookmarks propose
structure candidates. `outline_source` selects outline evidence; source review confirms the
actual outline, label range, body, layout and membership. Captions remain separate units.
Typography resembling body text can overrule a detector title or caption proposal; the ledger
records such decisions in `structure.overruled_labels` for the reviewer's role check.

An asset no text block references becomes a `visual` unit of its own (`p<page>-visual-<asset>`),
a `figure` unit for a figure and a paragraph otherwise, placed after the last body chunk that
ends above it. A `figure` or `table` unit made from a native block that holds only
placeholders (a stray label glyph inside the figure) takes the union of its assets' fragment
boxes as its `bbox`, as a `visual` unit does. The panels of one composite figure are one
asset with one raw crop of their complete outline: figure regions within 1.5 body-font ems of
each other with no text between them join when exactly one detected figure caption adjoins
them and none lies among them. When proximity glues separately captioned figures into
one cluster, the cluster splits by caption ownership — each panel follows the caption
it overlaps most — and each captioned group joins on its own. The PNG and SVG are two
formats of the same fragment, retaining the printed rows, columns, gaps and relative sizes.
Internal plot labels and graphical content stay in the crop; the shared caption remains
native text associated with its figure. Panels that each carry a
caption, panels with sub-captions or prose between them, and pages without a detected
caption keep one asset per region. A detector `table` box grows over the header
rows set above it: a row within two lines of the box's first row whose ink lies inside the
box's columns, that is not a caption (`Table 4.1.`), and that either shares the rows' native
block or is separated from them by a rule of the table's width belongs to the table, so the
column headings stay in the asset with the columns they head and never read as prose.

When assembly merges a chunk, references to it follow the surviving unit. The ledger's
`structure.list_items` records label and continuation candidates, including proposed text-column
positions. These do not establish `paragraph_id`, item membership or nesting; source review
records those relationships explicitly.

A bare rule — a glyph-free `mixed-region` at most 10 pt tall and at least three times as
wide as tall, whatever proposed it: a native drawing, a reviewer's region with
`visual-region-correction` provenance — is layout, not content: its unit is a `note` with
`render_policy: omit`, not translatable, kept in the ledger and never read; it takes the open
group as its parent (its own at the page top) and never opens one, so the prose after a
running-head rule starts its own paragraph. A region a
detector labelled (`PP-DocLayoutV2:*`) or an embedded image (`native-image`) is never treated
as a rule. Overlapping native drawings (the segments of a diagram) are clustered into one
region before regions are merged pairwise — the same result the pairwise merge reaches one
drawing at a time — so a page with hundreds of vector segments is prepared in seconds.

A printed equation label such as `(1.6)` beside a display is bound to the unit's
`equation_number` and removed from `source_text`; the checkpoint HTML shows it in the unit meta
line and the Markdown/HTML renderers re-emit each label from the saved equation metadata. A
label that shares its PDF block with the tombstone closing a proof (`□ (1.50)`) binds too; the
tombstone stays in the reading order, after the display it closes wherever MuPDF put its
block, and structure assembly attaches it to the paragraph the proof ends in. A native line that is a label and nothing else is text wherever it sits: it is
never notation (seeded by a detector box or not), never carries a formula across the line
end, and no display box owns its ink, so a crop never holds a label and a label cut into
`(6.` + `14)` cannot happen. A label the paragraph is still left holding at its start or end
binds to the neighbouring unnumbered display when the display's rows cover the label's
line. A label too wide to share a row is set on a line of its own. Set between two rows of
its display, it joins them: when exactly one unlabelled display ends within an em above the
label line and exactly one starts within an em below it, the two share columns and nothing
else — the label line of another display included — lies between them, they are one asset
with a fragment per row. Set just above or below
its display, it binds to the one unnumbered display within a line of it with nothing read
between them. A detector box whose rows carry two labels is cut through the widest ink-free gap
between the label rows, one display per label, and no stretched-delimiter column is chained
across the cut; a box whose ink spans both labels (one tall matrix) stays whole. A block that
holds several labels beside one formula keeps them in its text. A label that stays in prose
belongs to a block that preparation could not bind to a display asset; structure assembly
never merges such a label into the preceding paragraph.

A displayed block whose lines carry native prose (a cases formula with condition words) keeps
its rows: `source_text` separates them with `\n`, the Markdown edition emits hard breaks and the
HTML editions stack them as `display-row` spans; when the first row opens with an asset
placeholder followed by text (a stretched brace, an `X = {` head) that asset becomes the
`display-lead` column beside the rows. A row is a printed row: a native line break inside the
block whose next line continues the same baseline (a formula MuPDF split at a gap) is a space,
not a row. Translators keep one target row per source row
(`display-rows-mismatch` warning otherwise). Inline fragments are coalesced along a row only.

Label and paragraph-break candidates come from native glyph gaps, font changes and line
starts relative to the body column. Hanging text columns, indentation and paragraph spacing
help distinguish a wrapped line from a new paragraph or item. The ledger records label text,
`body_x` and proposed continuations for visual inspection. Reviewers also inspect starts the
candidate detector missed; numbers, years, cross-references and mathematical parentheses remain
body text unless the original establishes a label. No finite numbering grammar confirms a
paragraph's type, outline or membership.

A line of prose that starts mid-row continues its printed row. MuPDF opens a new block after
a tall operator (`∑` with limits, a big radical) and after the limits of an inline sum, and
the text that follows starts an em or two in — where a paragraph indent would be — or far to
the right. The planner finds the row's start by walking left through ink that shares the
line's vertical extent (a subscript joins through the operator it hangs from; the measured
ink decides, since MuPDF puts a radical's origin a text baseline away from its row) and,
where the row starts at the margin or at an open item's text column, reads the line as the
row's continuation: its x is the row's start where its own x would read as an indent, it
is recorded as a continuation candidate in `structure.list_items`. Item membership remains
a source-review decision. A line without a language word (the
limits themselves, a piece of a display) keeps its own x. Structure assembly's same-line
merge then keeps the sentence around the operator in one unit.

Paragraph white space is a structure boundary too, inside a PDF block or between two blocks,
for documents that space their paragraphs instead of indenting them. A chunk opens a paragraph
when the baseline gap above it exceeds the page's paragraph gap (`max(1.75 × font size, 1.45 ×
median line pitch)`) **and** the text line above it closes: it starts where text starts (the
margin, a label column or a paragraph indent), is mostly letters of language words in a text
face, and ends in terminal punctuation or stops short of the running text's right edge. Neither
condition alone counts: a full line a tall inline formula pushed down keeps its paragraph, and a
formula row (`∫ X dP`, a limit, a printed label), a tombstone beside a display or a detector
display row says nothing about where a paragraph ends, so a flush "provided …" clause after a
display remains a continuation candidate. Review confirms the actual paragraph boundary.

An `equation` unit without asset placeholders whose text shows no notation (an upright `Prob`
operator, a lone `otherwise.`) is native text: packets, Markdown and HTML render it — and its
translation — as text, not as a symbol sequence. Text with LaTeX commands, relations, digits or
Greek/operator characters, or a unit with `latex`, still renders as display math.

Glyphs of a mathematical face (CMMI, CMSY, CMEX, MSAM/MSBM, STIX, ...) that decode to control
characters are ink: CMEX encodes the integral sign as CR and big parentheses as LF, and a
display region owns them like any other glyph. A stretched delimiter assembled from pieces on
several baselines (⎧ ⎪ ⎨ ⎪ ⎩) is kept in one region; regions that had split it are merged and
record `stretched-delimiter-merged`. A piece is known by its Unicode value, by its CMEX slot,
or — in a subset font the PDF producer re-encoded, where a brace piece may decode to any
control character — by its shape (tall and narrow), which also keeps a big operator such as
`∑` or `∫` from counting as one; stacked pieces form a column only when each starts where the
previous one ends, so two integral signs at one x on consecutive display lines never merge
their lines. The review packet's `boundary_diagnostics` report
`math-ink-outside-ownership` when a symbol-face glyph on one of a displayed crop's rows (its
baseline within 0.6 × size of an owned inked glyph's, its centre inside the padded fragment
box) is owned by another asset, since the explicit export draws owned paths only and would
leave a hole; the descenders of the line above the crop are not a hole. `prose-boundary-in-math`
skips a bracketed phrase whose words are all declared `formula_conditions` of the asset.

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
`ρ(x) = {…`), including a row that is mostly a condition word (`0, otherwise.`) — the bracket
is the whole delimiter column, the extender pieces joined, not the one piece a row's baseline
happens to fall in — and every row a fraction bar spans directly above or below it, however
many words it holds (`surface area(U)` over the bar, `vol(B)` under it); a phrase is returned
to the paragraph as set-off prose only when it sits beside the whole formula, not when it lies
within the horizontal extent of the formula's other rows. A row that starts at the prose
margin and is mostly prose is the paragraph the box overshot into, as before; the margin is
the most common pen origin of the page's text-opened lines, so the pieces of one brace (each a
native line at one x) cannot move it into the formula.

An inline region owns such rows too. A cases block or a matrix set in running text (`G(x) = {`
in a list item, which the detector may label `inline_formula` or miss) is scanned per native
line, so the brace lands in the first row's run and every other row would become an asset of
its own, strung together by the commas the runs trimmed. When an inline math region owns a
stretched delimiter whose ink spans at least two font sizes, every visual line whose baseline
lies inside that ink, on the side the delimiter opens towards, belongs to the region: up to a
second tall delimiter of the same region (`\left( … \right)`), a tombstone, or a horizontal
gap of three ems that no other row bridges (the aligned condition column of a cases block is
bridged by the rows whose first column is longer; an equation number is not). A row that starts
at the paragraph margin and is mostly prose is a paragraph line the ink happens to reach and
stays outside. The rows' punctuation is the block's own; only sentence punctuation closing the
last row is left to the prose. The region becomes one crop (a `line-break-continued` head such
as `G(x) =` on the same rows joins it as one fragment), records `stretched-delimiter-rows`,
and its condition words are declared as `formula_conditions` like a display's.

Inline notation is collected per native line, and a text-face operator name set flush against
its argument's bracket (`Cov(`, `mean(`, `area(`) joins the run like a single letter or a known
operator does. A text-face accent (`ˆ`, `¯`) set over a mathematical base joins the base's run.
Bold letters flush against bold digits of the same baseline and size (`KP92`), or a bold phrase
enclosed in `[` `]`, are a citation key and stay prose; a bold single letter elsewhere is a
variable. A text-face digit smaller than the text-face digit before it, set on a raised or
lowered baseline and flush against it, is a script digit, and so are the digits continuing
it: TeX sets a power of a number in the text face alone (`2^{19937}`, `10^6`), so the script
opens the run, the number it is attached to joins it as its prefix and the notation after it
continues it (`2^{19937} − 1` is one asset). A superscript after a letter or a punctuation
mark is a footnote call, never an exponent — the same reading the footnote planner takes; a
script MuPDF emits as a native line of its own is not joined by this rule. A known operator name (`log`, `lim`, `dim`, `max`, `mod`; the `MATH_OPERATORS` set)
also continues an open run when notation or an opening bracket follows it (`lim_{ε→0} log c_ε /
log d_ε` is one run; `the log of` is prose), and opens one across the word space TeX sets after
it (`log x`, `−log P(D)`), whether that space is a text-face glyph or a math-face one — a
single letter after a space (`a x`) remains the article it is. A closing bracket in the text
face is trimmed from the run's end only when the closers outnumber the openers and prose
follows it (`(the space L^p(Ω))`); intervals count every bracket kind together, a bracket at a
line edge is never trimmed, since it may belong to an expression continuing on the next line,
and inside a detector region the brackets are balanced over the whole region while neighbours
are looked up on the glyph's own native line, so a superscript MuPDF places in the next block
(`O(n^{-1/2})`, its `2` and `)` on a line of their own) keeps its closing bracket. Sentence
punctuation at a run's edge (`.`, `,`, `;`, `:`, `?`, quotes) is prose — a text-face `!` is
prose only when the block ends with it or a text-face capital follows it (on the line or the
next line of the block), since a factorial is set in the same face and reads on (`n! ways`,
`n!,`); a formula that is all the ink of its block (equation tags aside) keeps a block-final
`!` — except a math-face `,` `;` `:` closing a line whose next
line opens with notation on the same row (the comma of `x_1,` `x_2` across a line break stays
in the run); a text-face `-` closing a line before a lowercase text-face letter is prose.

TeX breaks an inline formula only after a relation or operator. A native run that closes its
line with one (`f(λ) >`, a summation sign) continues in the run that opens the next line, even
when that run starts with a digit or bracket (`0`), and the two halves become one asset with
one fragment per line (provenance `line-break-continued`). A half that a display region
absorbed stays where it is; text-adjacent placeholders are still coalesced afterwards.

The space at an inline-asset boundary is the printed one. TeX sets the glue around notation,
which MuPDF reports as a space glyph or not by its own threshold — and reports the kern before
a period as a space — so at every boundary between an asset and its native neighbour on one
native line (same baseline, comparable size, measured ink) the gap between the asset's
outermost inked glyph and the neighbour decides: 0.15 em or more is a word space, less is none
(`{{Φ}} the`, `{{F}}.`); punctuation, a closing bracket or quote after the asset and an opening
one before it never take a space. A script of the asset at the boundary (`C^∞ in`, the `∗`
of `X^∗ is`) is measured at the prose's size against a quarter em, since the script space and
an italic side bearing alone can reach 0.15 em (`F_t-adapted` stays joined); a script on the prose side
(`T_high`), another baseline at a comparable size or an unmeasured glyph leaves the text
layer authoritative. Prose-to-prose spacing is the text layer's.

The explicit export keeps a horizontal rule (a fraction bar, a table rule, an overline)
whenever it lies inside the fragment's box (± 2 pt) and overlaps the owned glyphs' horizontal
extent, wherever the nearest glyph box lies; a degenerate path (empty `d`, move-to only)
prints nothing and is skipped rather than failing the page's precise export. Crops are
exported once per export identity, so a rule an earlier build dropped reappears only when
the fragment's identity moves or its crop directory is removed.

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
When the document prints neither, the hyphen stays only if both halves (three or more
letters) are words the document prints on their own (`finite-state`, not `pas-sion`); a
capitalised second half is a name compound (`Fokker-Planck`, `Borel-Cantelli`); `and`, `or`
or `nor` after the hyphen is a suspended hyphen (`left- and`); an asset placeholder before the
hyphen makes a compound with the word after it (`{{σ}}-algebra`). Attestation counts whole
words only, never a hyphen-adjacent half. A suspended hyphen inside a line (`pre- and
post-processing`) is never altered. Block seams inside a paragraph follow the same rules.

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
creation-time value (the identity hash without conditions declared after creation).

Reviewed `asset_crops` are another explicit export path. Their directory hashes bind the crop
declaration, source hash, page, original glyph IDs and fragment count, using export method
`reviewed-page-crop-v1`. The override ledger and `reviewed-page-crop` provenance marker retain
this evidence; these exports do not use the automatic crop's `evidence.json` contract. The
asset ID stays fixed, while its content hash also binds the crop provenance markers. Consumers
must use each fragment's recorded paths rather than infer them from an asset ID.

Crop directories under `derived/assets/fidelity/<hash>/` are addressed by export identity, so a
changed geometry writes a new directory. Once `source extract --replace` or an override import
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
receipt. `source extract --replace` reports such pages in `retained_receipt_pages`; a page
of the run whose fingerprint moved, or a page outside the run whose receipt depends on a
re-prepared page (continuation or container closure, read from the record before the run and
from the re-prepared units, so a page the run reaches only through an edge it added is found
too), loses its receipt explicitly and is listed in `invalidated_pages`, in or outside the
run. The ledger's `structure.document_profile.guidance_sha256` records the page guidance at
preparation; when the page's receipt was reviewed from a packet whose guidance for the page
equals the current guidance, a re-preparation keeps the old record instead of writing a new
digest, so `--replace` retains exactly the receipts that `source verify` accepts. `littrans doctor` prints the installed `build` (`plugin_version`,
`build_digest`, `package_path`) for comparison with an artifact's `generator`.

### Replaying reviewer overrides

A page ledger records the reviewer's `source_overrides` and, since it was imported, their
`source_overrides_origin` (`packet_id`, `reviewer`). `source extract --replace` on such a page
replays the recorded override: the human decision is reproduced exactly (the recorded detector
result is reused, as for every re-prepared page; a page whose recorded result is gone, or set
aside by `--redetect`, is replayed on the fresh detection and also listed in
`redetected_override_pages` when that detection differs), never silently replaced by a fresh
derivation. The
result lists these pages in `replayed_override_pages`. Pass `--discard-overrides` to re-derive
them from the current extraction rules instead (`discarded_override_pages`); a replay that no
longer applies (a pinned asset gone, glyph IDs changed, a `units` block naming an asset the page
no longer cuts) fails the whole transaction with the page number, the asset IDs that are missing
or unreferenced (`not cut on this page any more: …; cut but unreferenced: …` — the new ID of a
moved asset) and that hint, so re-import the review file with the new IDs or discard deliberately.

## Source review packets, decisions and overrides

### Packet and receipt bindings

See the [CLI contract](cli-reference.md#packet-and-receipt-bindings).

### Decision fields

See the [CLI contract](cli-reference.md#decision-fields).

### Override contract

See the [CLI contract](cli-reference.md#override-contract).

### Formula-contained language and original page overflow

See the [CLI contract](cli-reference.md#formula-contained-language-and-original-page-overflow).

## Narrow original-glyph corrections

For an exact visual recrop, use [asset_crops](cli-reference.md#override-contract) and
`source preview-review`. It preserves ownership separately from the output box, works on
each fragment of math, figures and tables, and replays through ordinary source extraction.
Automatic fallback now uses measured ink when complete and when no graphical evidence would
be discarded; incomplete measurements retain conservative bounds and the review warning.

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

Reviewed `paragraph_id`, `item_id/parent_item_id` and `parent_id/container_role` describe
paragraph fragments, item scope and semantic containers independently. An item's equations
and later paragraphs retain its item ID; a new paragraph has its own paragraph ID. Preparation
may propose statement or proof groups from typography and boundary cues, but source review
confirms their scope, including cross-page membership and closing marks.

Children retain stable IDs, equation labels and markup. Batching and audit dependencies use
the reviewed relationships; changing a member can invalidate evidence for its logical group.
`section_id` supplies heading context without requiring an entire section in one batch.

Continuation dependencies skip omitted running material and footnotes and require physically
adjacent PDF pages. A fragment at the boundary of a noncontiguous page selection remains a
fragment; it must not be joined to the next extracted page across a gap.
