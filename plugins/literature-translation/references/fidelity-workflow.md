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
and interpreter); `project tracked` reports a result that is not committed. `source prepare
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
reviewer approved), while `source prepare --replace` replays the override on the fresh
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

A chunk's kind starts from the layout detector's label for the box that holds it (`paragraph_title`
→ `heading`, `figure_title`/`table_caption` … → `caption`), but a heading or caption label stands
only where the chunk's typography bears it out. A chunk set like running text — median letter
size within 5 % of the body size, at least 60 % of its letters in the page's body face or a
non-bold italic, not capitals or small capitals throughout, starting at the margin or a
paragraph indent — is a `paragraph`, however confident the box: an italic step line ("*Step 2.*
…"), a run-in theorem line whose statement follows on the same line, a sentence that mentions a
figure ("Figure 10.2 shows …"). A caption also stands when it is set smaller, opens with a label
in a face of its own (`**Figure 1.1.**`, small capitals), stands clear of the text start
(centred or indented), or opens with a closed label (`Figure 3.`, `Table 2:`); a heading when it
is larger, bold or in a face of its own, in capitals, or stands clear of the text start. The
overruled label is recorded in the ledger (`structure.overruled_labels`, present only when one
was overruled) and listed for the reviewer's role check. A title box over a block set like
running text no longer keeps that block from being cut at an indent or a label.

An asset no text block references becomes a `visual` unit of its own (`p<page>-visual-<asset>`),
a `figure` unit for a figure and a paragraph otherwise, placed after the last body chunk that
ends above it. A `figure` or `table` unit made from a native block that holds only
placeholders (a stray label glyph inside the figure) takes the union of its assets' fragment
boxes as its `bbox`, as a `visual` unit does. The panels of one composite figure are one
asset with a fragment per panel, read row by row: figure regions within 1.5 body-font ems of
each other with no text between them join when exactly one detected figure caption adjoins
them and none lies among them. When proximity glues separately captioned figures into
one cluster, the cluster splits by caption ownership — each panel follows the caption
it overlaps most — and each captioned group joins on its own. Panels that each carry a
caption, panels with sub-captions or prose between them, and pages without a detected
caption keep one asset per region. A detector `table` box grows over the header
rows set above it: a row within two lines of the box's first row whose ink lies inside the
box's columns, that is not a caption (`Table 4.1.`), and that either shares the rows' native
block or is separated from them by a rule of the table's width belongs to the table, so the
column headings stay in the asset with the columns they head and never read as prose. When structure assembly merges a chunk into the
one before it, every later chunk whose parent it was follows the survivor, so no `parent_id`
names a unit that does not exist; an enumerated item (`(a)`, `(ii)`) hangs from the paragraph,
statement or proof that introduces the list wherever its label sits — after paragraph white
space or at a paragraph indent — never from a sibling item, and its later siblings return to
that same parent whatever group an item's own indented continuation paragraph opened in
between (an enumeration whose first item opened a group of its own, at a page top, leaves
each item its own group; a heading, statement, proof, run-in or numbered label, or prose back
at the margin after white space closes the enumeration). A chunk the planner recorded as an
item's continuation (`structure.list_items … continues`) returns to the item's group whatever
opened in between.

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
line and the Markdown/HTML renderers re-emit `(N)` themselves, like heading and list markers. A
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
page whose most common line start is the item column itself. That column is not the page's
margin when prose sits beside the list: when the most common line start is the text column of
the page's labelled lines and at least two text lines with a language word (not labels, not
running material) start left of those labels, the most common such start is the margin, so a
paragraph indent between the margin and the labels still opens a paragraph (and sets
`indent_style` and page-top continuations); a page of exercises with no prose beside them keeps
the item column. Prose resuming at an outer item's
column after a nested list starts its own chunk. The ledger's `structure.list_items` records
`{"label", "body_x"}` for each label chunk and `{"continues": <chunk>}` for each continuation
chunk; the key is absent on pages without labels. A line indented from an open item's text column
(0.8–2.8 em right of it, carrying a language word) opens a paragraph of that item: its chunk is
recorded as `{"continues": <item>, "paragraph": true}`, stays in the item's group and is never
merged into the item's text. Bullet items keep their own rule (a bullet
always hangs, so prose returning left of the bullet column ends the item). A labelled list
ends the same way where the page shows it hanging — a line of an item at its text column, or
labels of different widths aligned on one column (`(i)`/`(ii)`/`(iii)`): a line that starts
more than half an em left of the open item's label, after a line closing with `.`, `!` or `?`,
and whose first text-face letter is a capital, opens a chunk of its own (in the item's block
or as the next block) that assembly never merges into the item. Its group is the one it would
have had anyway (the list's introducing paragraph). In a list whose items wrap to the margin,
such a line stays the item's wrap.

A line of prose that starts mid-row continues its printed row. MuPDF opens a new block after
a tall operator (`∑` with limits, a big radical) and after the limits of an inline sum, and
the text that follows starts an em or two in — where a paragraph indent would be — or far to
the right. The planner finds the row's start by walking left through ink that shares the
line's vertical extent (a subscript joins through the operator it hangs from; the measured
ink decides, since MuPDF puts a radical's origin a text baseline away from its row) and,
where the row starts at the margin or at an open item's text column, reads the line as the
row's continuation: its x is the row's start where its own x would read as an indent, it
continues the item whose column the row starts at (recorded in `structure.list_items`), and
it never closes the item the row's first line opened. A line without a language word (the
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
display stays in its paragraph's group. The flag is not recorded in the ledger; a page without
such a break keeps the fingerprint it had before the rule existed.

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
`source_overrides_origin` (`packet_id`, `reviewer`). `source prepare --replace` on such a page
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

`source review-packets` writes `packets/source-<hash>/packet.json`, `review-template.json` and
`coverage.html`, and its output adds the `dispatch` (host, `source-review` role, model, effort) for
the `literature-source-reviewer` subagent that reviews, corrects and imports the pages. Coverage HTML and its referenced page images are bound by the packet
`visual_report` manifest; relative image URLs keep reports portable. A damaged report is rebuilt
under a new packet identity and cannot silently restore prior approval.

Source-review receipts bind the decision, reviewer, source, page fingerprint and original packet
identity/hash with `receipt_sha256`. Submissions must echo `visual_report_sha256` after
inspection, and receipts retain it. Approval consumers (`source verify`, batch creation,
workflow coordination) verify the receipt and packet, compare the structure guidance of the
page in the packet's embedded profile with the current profile (`fidelity-source-unverified`:
`source structure guidance changed since review for page N (handling_rules: …)`), then recheck
the visual decision conditions. Legacy receipts without these bindings require a fresh visual
review import; no automatic approval migration is performed.

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
`formula_conditions` (asset, text, box), `grouping_pending` asset IDs, `boundary_diagnostics`,
`findings` and `structure_checks` — so the review confirms a list instead of guessing from crops.

`structure_checks` holds the page's semantic decisions, one row each (the coverage report prints
them per page). `roles`: every read unit prepared as a `heading` or `caption`, every unit that
opens with a statement, run-in or proof label, and every unit whose detector heading/caption
label preparation overruled (`detector_label`), with its `kind` and an excerpt. `joins`: every
native text block whose text was joined into another unit rather than becoming one, with that
unit and an excerpt. Page flags say nothing about which of these was looked at, so each is
confirmed on its own: `confirmed_roles: [{"unit_id", "kind"}]` names every listed role with the
kind read on the original — face and size against the page's running text, indent and vertical
space, the same form on neighbouring pages — and `confirmed_joins: [{"block"}]` every listed
join after checking that no paragraph break (an indent, white space, a new list item) separates
the block from the text before it. A page passes only when both lists cover their rows
(`role-unconfirmed`, `join-unconfirmed`), a confirmed kind equals the recorded one
(`role-disputed`: correct the page with a `units` override instead) and no confirmation names a
unit the list does not hold (`role-not-listed`); an entry in another shape is an error naming
the page. A packet made before `structure_checks` existed asks for neither list, so receipts
bound to it keep passing. List containers are flat: a lead-in paragraph is the parent of its
items and of the displays and explanation paragraphs inside them; to hang a display from the
item itself, use a `units` override.

The approval gate and the checkpoint's attention list share one predicate
(`page_review_findings`): an approved page is a page that needs no attention. A page passes
only when every required flag is `true`, `issues` is empty, no `override` is present and no
finding remains: `grouping-pending` (an asset with `grouping_pending` that the decision does
not list in `accepted_grouping_pending: [{"asset_id", "reason"}]` with a non-empty reason; an
entry in another shape, such as a bare asset ID string, is an error that names the page rather
than a silently ignored acceptance),
`undeclared-formula-language` (language inside a `math` crop that no formula condition
declares) and `recoverable-prose-in-image` (a `math`/`mixed-region` asset owning six or more
undeclared words). A receipt that does not pass records the reasons in `failures`, and the
import result lists them in `rejected_pages`. A `mixed-region` image is never textual coverage
of recoverable paragraphs: split the source region and obtain a fresh packet.

Overrides are applied before approvals. Decisions whose dependency fingerprints changed because
another decision in the same import corrected a page appear in `deferred_pages`; corrected pages
appear in `changed_pages`; pages outside the review whose receipt depended on a corrected page
(continuation or container closure, in the graph before the correction or in the one it
produced — units re-parented to a container on the page before reach that page only through
the new edge) lose that receipt and appear in `invalidated_pages`. The three lists are
disjoint and together are what needs a new packet and review, never an immediately stale
receipt.

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
- The override is the page's whole correction. It replaces the override the page's ledger
  already records (`source_overrides`), block by block, and it is what `source prepare
  --replace` replays afterwards. A decision whose `override` omits a block the ledger records
  (`regions`, `units` or `page_canvas_bbox`) is refused — `page 64: the recorded override
  carries units (21 entries) that this override omits; carry it forward or set "units": null
  to drop it` — because correcting one formula with `regions` alone would otherwise retire the
  page's pinned `units` without a word. Carry the block forward, or state the drop with `null`
  (the ledger then records the override without it). An override of nothing but `null`
  blocks is not a correction: re-derive such a page with `source prepare --pages N --replace
  --discard-overrides` and review a new packet.
- `regions` replaces the detector/native proposals for the page. Each region names `kind`
  (`math`, `table`, `code`, `figure` or `mixed-region`) and either a single `bbox` in PDF points
  or `fragments: [{bbox, glyph_ids?}, ...]` for one logical asset with several ordered fragments
  on the same page (cross-page elements use continuation links instead). Optional fields:
  `id` (`[A-Za-z0-9][A-Za-z0-9._-]*`, default `a-p<page>-<content hash>`), `glyph_ids` (explicit
  native glyph ownership; see the glyph corrections section), `display`, `grouping_pending`,
  `provenance` and `formula_conditions` (any `math` asset, inline or displayed; a `math` region
  that omits the key is declared automatically, an explicit list is kept as written). A region
  that names glyphs is exported from their paths; one that names none — `"glyph_ids": []` on a
  rule, a figure frame or a page number box, or no key at all — owns nothing and is a raw crop
  of its box, and keeps the `kind` it declares. Only when named glyphs cannot be isolated does
  the export fall back to a raw crop with `precise-export-unavailable:…` in its provenance and
  `grouping_pending` set; a `math` region then becomes a `mixed-region` (a raw crop is not an
  isolated expression), a `figure` or `table` stays what the reviewer said it is.
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
  assembly and inline-fragment coalescing. Inline fragments along a row are coalesced in this
  channel too: a `units` block may reference the coalesced asset (one ID, as the packet lists
  it) or its constituents; a coalesced asset the block does not reference is restored to the
  assets it was made of before the reference check. A recorded unit is filled with the same
  optional keys structure assembly passes before its `source_hash` is computed, so identical
  content hashes identically whichever channel wrote it: the final hash is
  `hash({prepared_source_hash, asset_content_hashes})` over the unit payload's hash and its
  assets' `content_sha256`, deterministic for the same content.
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
inferred statement, and so does prose opening after paragraph white space — unless it resumes
after the statement's enumerated clauses, when it is the statement's conclusion and keeps its
group, or it is an indented paragraph set in the statement's own italic face (a theorem body
at least 60 % italic continued by `*Conversely, if …*`), which continues the statement and is
never merged into the unit before it. An upright container's later paragraphs (an example
whose plain indented paragraphs still belong to it) carry no typographic evidence and stay a
source-review decision, as does container membership across a page edge. A numbered label unit (`1.11. Let …`, an exercise or numbered item) opens a
parent group of its own unless a statement is open, in which case it joins the statement like a
bracketed clause; bracketed clauses (`(a)`, `(ii)`) join the paragraph or statement that
introduces them, and the item's displays and continuation chunks join its group. A label unit
is never merged into the unit before it, nor is a chunk opening after paragraph white space
(it starts a group like an indented paragraph; a display after the white space stays a child
of its paragraph); `footnote` and `bibliography` units open their own group and never merge
with prose in either direction, while the wrapped fragments of one recognised footnote still
merge. A word hyphenated across the seam of two merged
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

Batches are cut when translation starts, at the user's request, never as the last step of source
preparation: the user may still correct verified source, and a batch freezes the units it was cut
from. `batch create PROJECT --pages PAGES` cuts verified pages into batches of about 900 source words
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
copied MathJax runtime and original SVG/PNG files (fragments prepared by the 0.6.0 build may also
carry a per-region `original.pdf`; it is no longer written, linked or copied, and `source prepare --replace`
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
host environment reported under that dispatch value (stored as is, unverified, never gated). A packet that
records no model or effort dispatched on the host's own default, so the echo of the absent field is omitted. Candidates name `asset_id`, `format` and `content`; `status` is
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
  titles are folded before matching, and so is `source`. A quoted title is a double-quoted phrase
  of at least two words whose words are capitalised except `a an and as at but by for from in
  into nor of on or over the to via vs with` (`“Binding Theory”`); every quotation of a
  `bibliography` unit counts as one. A quoted term (`“strict mode”`) is matched. Folding covers
  precomposed, combining and TeX spacing accents (`Hölder` ≡ `H¨older`, `Lévy` ≡ `L´evy`), ligatures, curly quotes and apostrophes
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
New workflow packets record host/model/reasoning_effort in their manifest and identity, resolved from
the stage's own role in `agent_models.<host>`; each role carries its own model and effort. Either may be
absent, which dispatches on the host's default and is reported as an advisory, never refused. The
roles are `translate` (also `revise`), `transcribe`, `audit`, `asset-audit` and `source-review`; each
stage runs in a fresh subagent of its agent (see host-runtimes.md). Source-review material carries
its dispatch beside the packet, never inside it: `workflow packet --stage source-review`,
`source review-packets --host HOST` and the `source-review` task of `workflow next` report the
role's model and effort, while the source packet identity stays bound to content alone. Legacy
manifests remain readable with absent policy fields; create fresh packets for an explicitly bound
dispatch policy. `workflow status --host` uses the same override as next/packet, and
`project models PROJECT --host HOST` reports the resolved policy with its advisories.

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

`workflow next` and `workflow status` check batch coverage over the coordinated scope: the
pages of the coordinated batches (the bounded or series range for `next`, the requested IDs
for `status`) and the reading-order span between them. A renderable unit no manifest covers
inside that scope stops the wave (`unbatched_units=…`: refresh or create batches); pages
outside it that hold such units — a chapter extracted but not yet batched — are reported as
`unbatched_pages` and do not block coordinating the batched chapters. Formal rendering keeps
its own check over the pages it renders.

## Rendering

A project without any transcription candidate renders originals-only automatically; render QA
records `originals_only_reason: no-transcription-candidates`. For an explicitly all-original
reading edition of a project with candidates, render with `--originals-only`
(`originals_only_reason: requested`). Either way original-image representation is forced in both
Markdown and bilingual HTML without changing any candidate or review, and the candidate MathJax
bootstrap is omitted. The edition header, `*.quality.md` and `render-qa.json` (`rendered_status`)
reflect the lowest record status among the rendered units, not the project-wide status. Formal
dependency cover selection considers only current QA/audit/external evidence.

A paragraph continues across a page edge when the sender's `continued_to_next` is set (its
last line ends mid-sentence), or when the receiver's `continues_from_previous` is set and the
sender's text does not end in terminal punctuation; the receiver's flag is never set on a unit
that opens with a bold run-in label, a theorem statement, `Proof` or a list label, nor on one
whose first letter is a capital (`This gives`, `Then` open a sentence; `where the notation …`
continues one; a script without letter case keeps the geometric reading). The flags express
a continued sentence; a container that continues on the next page (a proof, an exercise) is
recorded by a reviewed `parent_id` override, never inferred. A figure, table or caption set
at the page top or bottom is a float: the flags are decided on the first body unit after the
floats at the top and the last one before the floats at the bottom. Batching and audit
closure read the same pair of flags, across any floats between the sender and the receiver.

Reading output appends image-language companions after a complete continuation chain; footnote
companions remain inside their Markdown definitions. Markdown footnote calls and definitions use
unique labels derived from the referenced source unit ID, so repeated numbers on different pages
do not collide; definitions are indented after asset/companion expansion, and continued table
fragments with independent footnote scopes remain separate. Continued-table rendering includes
companions from every fragment, using each fragment's original source context for labels and
footnote scope. Code/math literals remain literal, including dollar and backslash inline/display
delimiters and multiline backtick/tilde fences.

Rendered pages under `output/` link the original page images, the source PDF (`#page=N`) and
the copied original assets by relative, percent-encoded paths, so the same tree renders the
same bytes on every host and a rendered checkpoint can be compared across machines; only a
source PDF kept outside the project root is still linked by a `file:` URI.

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
| `.gitignore` (project root) | user | keeps the source PDF, generated `output/` files, the layout runs' `*.request.json` and `*.log`, per-asset `original.pdf` and unreferenced source packets out by default; keeps `.littrans/work/` and the layout results in and the lock out; an existing file only gains the `.littrans/*` / `!.littrans/work/` pair, and a file that spells it `/.littrans/*` already has it |
| `.gitattributes`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `PLUGIN-ISSUES.md`, `docs/{HISTORY,DECISIONS,TERMINOLOGY,REVIEWS}.md` | user | LF policy for the record (`*.cmd` CRLF, `*.sh` LF), handbook, operating manual (`CLAUDE.md` imports `AGENTS.md`), defect ledger and the four records — headings plus one line each on what belongs there, nothing document-specific |
| `tools/lt.py`, `tools/lt.cmd`, `tools/lt.sh` | user | launcher: `LITTRANS_PLUGIN_ROOT`, else the recorded plugin root or the same path under this user's home while it exists, else the install of the client running the session (read from its environment signals; Claude Code's `installed_plugins.json` record, otherwise the highest version in that client's cache), else the newest sibling of a recorded root outside every cache, else every client's install by version (numeric, pre-release aware, build metadata as a later build); executable on POSIX |
| `docs/LITTRANS.md` | **plugin** | what the installed build guarantees — version, build digest, term semantics, audit context parts, stale reasons, stages, wave limits, status order, QA version — rendered from the package constants; regenerated by `--refresh`, never hand-edited |

`project tracked PROJECT` derives the record from the data — `project.yaml`, context and
glossary files, `derived/units.jsonl`, `fidelity-assets.jsonl`, `provenance.json`, the page
ledgers, the layout results (`derived/fidelity-layout/<fingerprint>.json`), every asset crop
and its `evidence.json`, review templates, the source packets page receipts name (live
dependencies), reviews, external dry-run records, page evidence, audit ledgers, batch files,
translations, QA reports and `.littrans/work/` payloads — and the excluded set (source PDFs,
generated `output/` files, the layout runs' requests and logs, per-asset `original.pdf`,
`.littrans/state.json`). `project.yaml` and `derived/provenance.json`, which initialization
always writes, are required even once deleted, like the scaffold files and receipt-bound
packets. A nested project's check also covers the record root's top-level files, `docs/` and
`tools/`; a PDF there is excluded only when it is the configured source or the repository's
`.gitignore` excludes it. Unreferenced packets' `packet.json` and `coverage.html`, and
`output/.gitkeep`, are optional: they may be tracked or ignored. Git (`ls-files`,
`check-ignore`) reports a required record file that is ignored or uncommitted, an excluded file
that is tracked, and any file in neither state; the exit code is 1 on any problem. `project rebuild`
copies `docs/` alongside `context/` and `glossary/` and refreshes `docs/LITTRANS.md`.

### Several hosts, one record

The record names no machine: `project.yaml` records a PDF inside the project relatively
(`source/<name>.pdf`), page ledgers and packets carry project-relative paths and content
digests, layout results key pages by image content, dry-run records name their packet
relatively, rendered pages link relatively, and every file the plugin writes is UTF-8 with LF.
A project therefore moves between a Windows and a Linux host through an ordinary git remote
without a shared file system. What the hosts must agree on:

1. **Pull before writing, push after.** The write lock (`.littrans-write-lock/`) protects one
   tree, not the remote: one host writes the project at a time; the other pulls before its
   turn. A conflict in a record file is a sign that both wrote — resolve it by taking one
   side whole, never by merging JSON or JSONL by hand, then rerun `source verify` and
   `workflow status`.
2. **The same plugin build.** `doctor` reports `build.plugin_version` and
   `build.build_digest`; ledgers, packets and results record the `generator` that wrote them.
   Upgrade every host to the same build (or accept that pages re-prepared on the newer build
   may cut differently, as `MIGRATING.md` describes) and refresh `docs/LITTRANS.md` once.
3. **The PDF on every host, never in git.** Copy the source PDF to `source/<name>.pdf`
   (the path `project.yaml` records) on each host; `source verify` checks its hash.
4. **The layout runtime only where pages are first prepared.** Re-preparing, override
   replay and verification run on the recorded results, which the record carries; a host
   without MinerU can `--replace` every recorded page and only fails on a page that has no
   result (or with `--redetect`).
5. **`project tracked` before every commit.** It names a record file that is uncommitted or
   ignored (a new layout result, a live packet to re-include by name) and an excluded file
   that slipped in (`output/`, a PDF).
6. **Windows checkouts:** `git config core.longpaths true` (packet payloads nest deep) and
   keep `core.autocrlf` irrelevant by relying on the generated `.gitattributes`; a project's
   `tools/lt.sh` needs its executable bit once (`git update-index --chmod=+x tools/lt.sh`).
7. **The launcher resolves the plugin per host and per client**: `tools/lt.py` tries
   `LITTRANS_PLUGIN_ROOT`, the recorded root (or the same path under this user's home) while
   it exists, the install of the client running the session, the newest sibling of a recorded
   root outside every cache, then every client's install by version, so a project scaffolded
   on one host runs on the other once the plugin is installed there, and a Codex session never
   runs a leftover Claude Code cache. The launcher is user-owned: after an upgrade of the plugin
   delete `tools/lt.py` and run `project scaffold` to regenerate it.

## Resume and recovery

Persist successful responses before parsing or import. A cached response is reusable only for the
same input, model, prompt and relevant output fingerprints. Recover serialization failures
offline; do not pay for an identical successful response again. Imports are idempotent, but
changed source or packet hashes require fresh evidence. Record actual host/model metadata and
measured usage; token or monetary costs unavailable from the host stay unknown.

After interruption, ask status for the frozen batch IDs, recover unimported successful responses,
then dispatch only missing stages. Preserve unresolved original-image assets after translation
rendering; this is a recoverable checkpoint, not a failed reading artifact.
