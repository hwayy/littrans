# Changelog

All notable distributed changes to LitTrans are recorded here. Versions follow semantic
versioning and correspond to Git tags named `v<version>`.

## [0.6.1-dev.2] - 2026-09-23

### Fixed

- A detector heading or caption label no longer decides a unit's kind on its own. A chunk set
  like running text — body size, the body face or its italic, at the margin or a paragraph
  indent — is a paragraph: an italic step line, a run-in theorem line and a sentence that
  mentions a figure were published as headings and a caption (LT-086). Headings keep their
  label when set apart by size, a bold or other face, capitals, small capitals or position;
  captions also by a smaller size, a label in its own face, an indent or a closed label
  (`Figure 3.`). Overruled labels are recorded in `structure.overruled_labels`.
- A list-heavy page keeps its prose margin. When the items' text column was the most common
  line start, paragraph indents left of it read as no indent at all, so two paragraphs merged,
  a lead-in joined the paragraph before it and a page-top continuation went unflagged (LT-086).
- A paragraph indented from a list item's text column opens a paragraph of that item
  (`structure.list_items … {"continues", "paragraph": true}`) instead of being merged into the
  item's text; it stays in the list's container.

### Changed

- Source review packets list each page's `structure_checks` — every heading, caption, run-in
  label and overruled detector label (`roles`) and every native block joined into another unit
  (`joins`) — and a decision must confirm each one (`confirmed_roles: [{unit_id, kind}]`,
  `confirmed_joins: [{block}]`). A confirmed kind that differs from the recorded one is
  rejected as `role-disputed` and corrected with an override. Packets made before this version
  ask for neither list, so existing receipts keep passing.

## [0.6.1-dev.1] - 2026-09-23

### Fixed

- Kept Markdown asset translations and reader notes after the complete continued paragraph,
  including sender-only page continuations; closed sentences and gaps now flush their notes
  at the actual paragraph boundary.
- Preserved regex group names, backreferences, flags, escapes and comments while folding
  glossary literals. Normalized punctuation cannot become character-class syntax, invalid
  patterns still fail validation, and the QA fingerprint invalidates older terminology checks.
- Included representation evidence in required review records and detected tracked source
  PDFs anywhere inside the project repository without reaching outside it.
- Normalized legacy Windows layout paths across platforms while preserving hash precedence
  and rejecting ambiguous basename matches.
- Kept the first formula beside its own condition unless source asset geometry verifies a
  delimiter spanning all display rows; source and translation use the same geometry.
- Restricted row absorption to recognized delimiters instead of all CMEX glyphs, keeping
  large sums, products and integrals from absorbing adjacent prose. Tall, narrow CMEX
  bracket glyphs remain supported alongside delimiter slots and control-character pieces.

## [0.6.0] - Unreleased

Development builds on the way to 0.6.0 carry a semantic-versioning pre-release identifier
(`0.6.0-dev.N`, through `0.6.0-dev.17`) that is bumped with every behaviour-changing commit, so
plugin caches keyed by version no longer share a directory between builds and `claude plugin
update` sees a change; the release drops the suffix.

### Changed

- Every dispatch role carries its own model and reasoning effort, and an unset one is a
  supported choice (0.6.0-dev.16). `agent_models.<host>` gave `translate` and `transcribe`
  a bare model string and made them share one `reasoning_effort`, so a role could not pair a
  cheaper model with a higher effort and the reviewer lenses could not be configured at all.
  Each of `translate` (which also covers `revise`), `transcribe`, `audit` and `asset-audit`
  now holds its own `model` and `reasoning_effort`:

      agent_models:
        claude:
          translate: {model: sonnet, reasoning_effort: high}
          transcribe: {model: sonnet, reasoning_effort: high}

  Leaving a role, a model or an effort unset no longer refuses a packet. `workflow packet`
  and `assets packet` used to raise *"Configure agent_models.<host>.translate and
  reasoning_effort ..."*, which blocked Cursor and Qoder out of the box — neither host lets a
  coordinator choose the model of a single dispatched task — and forced a pin on Codex and
  Claude Code users who would rather let the host decide. An unset value now dispatches on the
  host's own default, and the plugin reports the two mismatches as advisories instead:
  a value configured for a host whose launcher cannot take it (kept in the packet, never
  dropped) and an unset value on a host whose launcher can. Advisories appear on stderr, in
  the `dispatch_advisories` field of `workflow next` and `workflow status`, and in the new
  `project models PROJECT --host HOST`. Codex takes a per-dispatch model and effort, Claude
  Code a model only (its effort comes from the writer agents' frontmatter), Cursor and Qoder
  neither. `assets submit` still rejects an echo that differs from its packet, but a packet
  recording no model has nothing to echo. A `project.yaml` written in the old flat form is
  read unchanged and rewritten in the nested form on its next save; a misspelled host or role
  key, previously silent, is now named.
- The exponent of a text-face number is notation (0.6.0-dev.15). TeX sets digits in the
  text face inside mathematics too, so `2^{19937}` or `10^6` carries no mathematical font at
  all: nothing opened an inline run, and the `− 1` that followed opened one after a word
  space, so the page read `a period of up to 219937 {{asset}}` with an asset holding only
  `− 1`. A text-face digit that is smaller than the text-face digit before it, set on a
  raised or lowered baseline and flush against it, is now a script digit — as are the digits
  continuing it — and the number it is attached to joins the run, which then continues into
  the notation after it (`2^{19937} − 1` is one inline asset). A superscript after a letter or
  a punctuation mark (a footnote call) is untouched; a script MuPDF emits as a separate
  native line is not recognised by this rule.
- A detector table box takes the header rows set above it (0.6.0-dev.15). PP-DocLayoutV2
  often starts a `table` box at the rule under the column headings, so the headings fell
  to the paragraph around the table (`Class Spin Number of neighboring spin ups {{asset}}`)
  and the asset lost the column–heading correspondence. A row directly above the box
  (within two lines of its first row) whose ink lies inside the box's columns, that is not
  a caption, and that either sits in the same native block as the rows below or is
  separated from them by a rule of the table's width, is now part of the table; the box
  grows over it and over the next such row above. The block that held only the headings
  becomes the `table` unit itself.
- A correction's dependency pages are read from the graph after the correction as well as
  before it (0.6.0-dev.15). `import` and `source prepare --replace` settled the receipts of
  the pages the corrected page reached in the recorded graph only; an override that
  re-parented a page's units to a container on the page before (a Solution continued
  across the edge) reached that container's page only through the edge it added, so that
  page kept a receipt whose fingerprint had moved and `invalidated_pages` was empty until a
  full-range `source verify` found it. Both commands now settle, by name, every page the
  change reaches in either graph and report it in `invalidated_pages`.
- Workflow coordination checks batch coverage over its own scope (0.6.0-dev.14). `workflow
  status --batch-ids …` and `workflow next` used to refuse to run while any renderable unit of
  the record was outside every manifest, so a book whose later chapters were extracted but not
  yet batched could not be coordinated chapter by chapter. The check now covers the pages of
  the coordinated batches and the reading-order span between them — a unit recovered on a
  coordinated page or between two coordinated batches still blocks the wave with the same
  message — and both commands report the pages outside that scope that no manifest covers as
  `unbatched_pages`. Formal rendering keeps its own page-scoped check.
- The scaffolded launcher runs what the session's client installed (0.6.0-dev.14).
  `tools/lt.py` tried the client caches in a fixed order and took the first one holding the
  plugin, so a leftover `0.6.0` directory in the Claude Code cache outranked the
  `0.6.0-dev.13` Codex had installed, and a cachebuster build (`0.6.0-dev.13+codex.<stamp>`)
  did not sort as a version at all. A regenerated launcher reads the client running the
  session from the same environment signals workflow coordination reads, prefers that client's
  install (Claude Code's own `installed_plugins.json` record, otherwise the highest version in
  the client's cache), then the highest installed version across clients; version order accepts
  build metadata as a later build of the same version. `LITTRANS_PLUGIN_ROOT` and a recorded
  root that still exists keep precedence. The launcher is user-owned: delete `tools/lt.py` and
  run `project scaffold` to regenerate it.
- A numerator, a denominator or a case row that holds words stays in its display
  (0.6.0-dev.14). A row of a detector display box was judged by its share of prose words alone,
  so `surface area(U)` over a fraction bar became a prose fragment and the fraction lost its
  numerator; a row a rule spans directly above or below it is now the fraction's, whatever it
  says, and a row bracketed by a stretched delimiter is judged against the whole delimiter
  column, not the one extender piece its baseline happens to fall in. The prose margin the
  decision reads is the pen origin of text-opened lines, not the first ink of every native
  line: seven pieces of one brace, each a native line at one x, used to outvote the prose and
  moved the margin into the formula, and the case rows of `(1.53)` (`X is discrete-valued,`)
  were dropped as a paragraph the box had overshot into. The words a kept row carries are
  declared as formula conditions, as before.
- A decorative rule never parents the prose after it, and the page-top continuation flag
  means a continued sentence (0.6.0-dev.14). The running-head rule (an omitted `note` unit,
  first in the reading order) opened the page's group, so the first paragraph of the page
  hung from it; an omitted unit now takes the open group as its parent (its own at the page
  top, as recorded before) and opens none. `continues_from_previous` is set only when the flush
  first line also does not open a sentence — a first alphabetic character in upper case
  (`This gives`, `Then`) reads as a new sentence, a lower-case one (`where the notation …`)
  as a continuation; scripts without letter case keep the geometric reading, and the sender's
  `continued_to_next` still carries a sentence that ends mid-line whatever the receiver says.
  Container membership across a page edge (a proof or exercise that continues on the next
  page) remains a source-review decision recorded by override.
- Enumerated items hang from the paragraph that introduces them wherever their label sits,
  and a statement's italic continuation stays in the statement (0.6.0-dev.14). A bracketed
  item (`(a)`, `(ii)`) set at a paragraph indent opened a group of its own — the rule that a
  clause after white space belongs to its introducer applied only where the planner's margin
  happened to be the list's text column — so `(a)`–`(c)` after "This can be used to compute
  …" and the steps `(ii)`–`(v)` of a proof opened after `**Proof.** (i) …` were each a
  container; they now join the introducing paragraph, statement or proof, and a sibling's
  column is read from the planner's line start rather than a bbox a display widens. Items
  that open a group of their own (at a page top) still leave their siblings their own. An
  indented paragraph set in the statement's own italic face (`*Conversely, if …*` after a
  theorem's clauses) continues the statement instead of ending it, and is never merged into
  the unit before it; upright prose ends the statement as before. A chunk the planner
  recorded as an item's continuation (`structure.list_items … continues`) returns to the
  item's group whatever opened in between. Multi-paragraph upright containers (an Example
  whose later paragraphs are indented plain prose) are not decidable from typography and
  remain a review decision.
- A proof's tombstone reads after the display it closes (0.6.0-dev.14). The `□` of a
  `□ (1.50)` block kept the block's native position, which MuPDF orders before the display,
  so it merged into the paragraph before the display (`… Then □`); the residue now follows
  the display it labelled and is a paragraph of the proof's group.
- A recorded `units` override whose asset moved is refused, not crashed on (0.6.0-dev.13).
  The check that every page asset is referenced exactly once now runs before any unit is
  built, so an override that names an asset the page no longer cuts (a display that gained a
  space glyph on re-preparation) fails with the page and both IDs — `page 70: the recorded
  source override cannot be replayed (unit overrides must reference each page asset exactly
  once; not cut on this page any more: a-p0070-789080e39dd4; cut but unreferenced:
  a-p0070-d3f4ee3efed5); re-import its review file or rerun with --discard-overrides` —
  instead of a bare `KeyError` from inside the unit builder that named neither. The
  transaction still rolls back whole. A review file imported with such a reference is refused
  with its page number too.
- Enumerated siblings share the parent their enumeration opened with. A `(2)` item that
  follows the `(1)` item's own indented continuation paragraph hung from that paragraph (the
  group the indent opened) and rendered in its row; it now returns to the paragraph that
  introduced the list, as `(1)` did. Only an enumeration that hangs from an introducing
  paragraph or statement binds its siblings — items set as indented paragraphs of their own
  stay so — and a heading, statement, proof, run-in or numbered label, or prose back at the
  margin after white space closes it.
- Asset boundaries are spaced by the printed ink, not by the text layer (0.6.0-dev.12).
  TeX sets the glue around notation, and MuPDF reports it as a space glyph or not by its own
  threshold — and reports the kern before a period as a space. At every boundary between an
  inline asset and its native neighbour on one native line (same baseline, comparable size,
  measured ink), a gap of at least 0.15 em is a word space and less is none; punctuation, a
  closing bracket or quote after the asset and an opening one before it never take a space.
  `{{F}}.` and `{{Φ}} the` come out right; a subscript, another baseline or an unmeasured
  glyph leaves the text layer authoritative. A native line break inside a display block
  whose next line continues the same baseline (a formula MuPDF split into two lines) is a
  space, not a row break.
- Printed equation labels are text, and a display that holds two of them is two displays. A
  native line that is a label and nothing else (`(3.11)`, at either margin) is never
  notation, seeded or not, never carries a formula across the line and is never owned by a
  detector box: its ink stays out of every crop, and a label the paragraph is left holding at
  its start or end binds to the neighbouring unnumbered display's `equation_number` when the
  display's fragment covers its line. A detector box whose rows carry two labels is cut
  through the widest ink-free gap between the label rows, one part per label, and no
  stretched-delimiter column is chained across such a cut, so `(3.23)`/`(3.24)` stacked in
  one box are two `equation` units; a box whose ink spans both labels (one tall matrix) stays
  whole. A block that holds several labels beside one formula keeps them in its text.
- A line of prose that starts mid-row continues its printed row. MuPDF opens a new block
  after a tall operator (`∑` with limits, a big radical) and after the limits of an inline
  sum, and the text that follows starts an em or two in — where an indent would be — or far
  to the right. The planner now finds the row's start by walking left through ink that shares
  the line's vertical extent (a subscript joins through the operator it hangs from; the ink
  decides, since MuPDF puts a radical's origin a text baseline away from its row) and, where
  that row starts at the margin or at an open list item's column, reads the line as the
  row's continuation: its x is the row's start where its own x would read as an indent, it
  continues the item whose column the row starts at, and it never closes the item the row's
  first line opened. The sentence around the operator stays one unit (`… and ∑_{|j−i|=1}
  p_ij = 1, where i, j ∈ S` is one item), and a `(b)` item after a row MuPDF split at a
  radical still opens. A line without a language word (the limits themselves, a piece of a
  display) keeps its own x.
- Structure assembly keeps parents and groups coherent. When a chunk is merged into the one
  before it, any later chunk whose parent it was follows the survivor (no `parent_id` names a
  unit that no longer exists), and the group an item hangs from is still the introducing
  paragraph's when the item opens after paragraph white space (an enumerated item after a
  spaced paragraph no longer opens a group of its own, siblings never hang from a sibling).
- Page-edge continuation is read from both flags, as batching already does. A first body
  unit that opens with a bold run-in label, a theorem statement, `Proof` or a list label
  does not continue the previous page; the renderer merges a paragraph across the page edge
  when the sender's `continued_to_next` is set (its last line ends mid-sentence) even if the
  receiver's flag is not, and does not merge on the receiver's geometry alone when the
  sender's text ends in terminal punctuation.
- Hyphenated line ends: when the document prints neither the compound nor the joined word,
  the hyphen stays only if both halves (three or more letters) are words the document prints
  on their own (`finite-state`, not `pas-sion` or `Lorent-zian`); a capitalised second half is
  a name compound (`Fokker-Planck`, `Borel-Cantelli`); `and`/`or`/`nor` after the hyphen is a
  suspended hyphen (`left- and`); an asset placeholder before the hyphen makes a compound with
  the word after it (`{{σ}}-algebra`). Attestation counts whole words only, never a
  hyphen-adjacent half.
- Inline run edges: a math-face `,` `;` `:` at the end of a native line stays in the run when
  the next line opens with notation on the same row (the comma of `x_1, x_2,` before a line
  break); a text-face `-` at a line end before a lowercase text-face letter is prose; `?` is
  sentence punctuation like `.` (`!` is not, since `n!` is notation in the same face); a
  text-face accent (`ˆ`, `¯`) set over a mathematical base joins the base's run instead of
  staying in the prose beside a crop of the bare letter. Bold letters flush against bold
  digits of the same baseline and size, or a bold phrase enclosed in `[` `]`, are a citation
  key (`[**KP92**, Theorem 2]`), not bold variables; `mod` is a known operator name. A
  connective set off between two formulas of one display (`= νQ,   or equivalently   …`) stays
  in the display as a declared condition instead of being cut out as prose. A `figure` or
  `table` unit made from a block that holds only placeholders takes the union of its assets'
  fragment boxes as its `bbox`, as `visual` units do.
- Boundary diagnostics report what the export would actually lose. `math-ink-outside-ownership`
  needs a foreign glyph on one of the asset's rows (its baseline within 0.6 × size of an owned
  inked glyph's, its centre inside the padded fragment box) — the descenders of the line above
  no longer fire it — and `prose-boundary-in-math` skips a bracket pair whose words are all
  declared `formula_conditions` of that asset.
- The explicit glyph export keeps a horizontal rule inside the fragment's box (± 2 pt) whenever
  it overlaps the owned glyphs' extent, wherever the nearest glyph box lies (the lower rule of
  a double rule, a table rule two lines below its header), and skips a degenerate path (empty
  `d`, move-to only) instead of failing the whole page's precise export. Note the crop cache:
  an asset whose export identity is unchanged keeps its earlier crop.
- The override `units` channel hashes like the pipeline: a recorded unit is filled with the
  same eight optional keys structure assembly passes before its `source_hash` is computed, so
  identical content has one hash in both channels and a page's fingerprint no longer depends
  on which channel wrote its units. Inline fragments are coalesced along a row in the override
  channel too: a recorded override that references the coalesced asset (the pipeline's
  `(8.50)` as one ID) replays, and one that references its constituents replays as well —
  each coalesced asset the override does not name is restored to the assets it was made of.
- `project scaffold` reads a project `.gitignore` pattern by its path: `/.littrans/*` already
  present means the pair is present, no duplicate is appended.
- Region merging on a page with hundreds of vector drawings finishes in seconds: overlapping
  glyph-free `native-vector` regions are clustered first (union-find, the same result the
  pairwise loop reaches one merge at a time) and the loop caches each region's inked glyphs
  and sorted baselines instead of recomputing them per pair. A 710-drawing page went from a
  timeout to under six seconds with identical regions.
- Inline notation keeps its right half (0.6.0-dev.11). Inside a detector region, brackets
  are balanced over the whole region while neighbours are still looked up on the glyph's own
  native line, so a superscript MuPDF places in the next block (`O(n^{-1/2})`, its `2` and `)`
  on a line of their own) no longer loses its closing bracket. A known operator name in the
  text face (`log`, `lim`, `dim`, `max`, …) continues an open run when notation or an opening
  bracket follows it (`lim_{ε→0} log c_ε / log d_ε` is one asset) and opens one across the
  word space TeX sets after it, whether that space is a text-face or a math-face glyph
  (`log c_ε`, `−log P(D)`); `the log of` and `a x` stay prose. Assets that gain such a name,
  or the space glyphs a now-continuous run carries into a display region, change identity on
  re-preparation.
- An inline math region owns the rows a stretched delimiter it holds brackets
  (`stretched-delimiter-rows`): a cases block or a matrix set in running text, which was one
  asset per row strung together by trimmed commas, is one crop with its `G(x) =` head, its
  row punctuation and its condition words declared. Rows are the visual lines whose baseline
  lies inside the delimiter's ink, up to a closing delimiter, a tombstone or a wide gap no
  other row bridges; a paragraph line at the margin stays outside. A CMEX delimiter piece is
  also recognised by shape when a re-encoded subset font maps it to a control character, and
  stacked pieces form a column only when each starts where the previous ends (two integral
  signs at one x on consecutive display lines no longer risk merging their lines).
- The region-override channel changes only what the reviewer said. `"glyph_ids": []` on a
  region (a rule, a figure frame) is a raw crop that keeps the declared `kind`, not a failed
  explicit export that rewrote it to `mixed-region` and left it `grouping_pending`; the
  genuine fallback keeps a declared `figure`/`table` and downgrades `math` only. A bare rule
  is recognised by what it is (glyph-free `mixed-region`, thin and wide, not detector-labelled
  or an embedded image), so a reviewer's region with `visual-region-correction` provenance is
  omitted from reading like a native drawing instead of becoming a translatable paragraph
  that steals the page's first `parent_id`. `accepted_grouping_pending` entries that are not
  `{"asset_id", "reason"}` objects are refused with the page named instead of silently
  ignored. An override that omits a block the page's ledger records (`regions`, `units`,
  `page_canvas_bbox`) is refused with the block and its size named — carry it forward or
  state `"units": null` to drop it — instead of retiring it without a word.
- Source receipts bind the structure guidance of their own page, not the profile file
  (0.6.0-dev.9). `context/source-structure.json` gains `page_rules`: blocks of
  `handling_rules` scoped to a page spec (`{"label": "Chapter 2", "pages": "52-67",
  "handling_rules": {...}}`) beside the base rules. The guidance of a page is, per key, the
  base text followed by the covering blocks joined by a line break; `source verify` and
  `source import-review` compare that between the packet's embedded profile and the current
  one and refuse only a page whose guidance changed (`source structure guidance changed since
  review for page N (handling_rules: lists, headings)`). Probing further pages, notes, status,
  block labels and the file's formatting or line endings change nothing; a CRLF checkout, a
  reformatted file or a new chapter's probe no longer voids every receipt. The `sha256` in
  `document_structure` is a content digest, new page ledgers record
  `structure.document_profile.guidance_sha256` (the page's guidance) instead of the file
  hash, batch context carries the blocks covering the batch's pages, and `source probe` tells
  the agent where a new scope's rules go. New `source rescope PROJECT --packet ID --pages SPEC`
  restores the base rules to the text a packet embeds and moves the lines appended since into
  a `page_rules` block, so a profile extended in place for a later chapter verifies both
  chapters' receipts again.
- Rendered checkpoints and editions link the page images, the source PDF and the original
  assets by relative, percent-encoded paths, so the same tree renders the same bytes on every
  host; only a PDF outside the project root keeps a `file:` URI.
- The paragraph after a list item opens after the item's continuation line: a line starting
  within 4.5 ems of the margin, or in a chunk the planner placed as a list item or its
  continuation, is a text line for the paragraph-white-space rule (it used to count as a
  display beyond 2.8 ems, so the paragraph following `b) …` was merged into the item).
- An unreferenced figure keeps its reading position on a page whose page number was detached
  from the running head: running material no longer anchors visual elements, so the figure
  sorts between the paragraph above it and its caption and owns the caption again. The same
  rule moves an omitted decorative rule (a running-head line) that the detached page number
  pinned to the page end back to the top, where a page whose number is its own block already
  had it; the reading output is unchanged, but such a page's unit order — and fingerprint —
  changes when it is re-prepared.
- Layout detector results are part of the record. `derived/fidelity-layout/<fingerprint>.json`
  is tracked (the generated `.gitignore` and `project tracked` keep only the run's
  `*.request.json` and `*.log` out); a result's `images` map names page images by file name.
  `source prepare --replace` cuts every page whose ledger records a result the directory
  still holds on that result — override page or not — and calls the detector only for the
  pages without one, so a rerun on another build or host reproduces the recorded pages and
  keeps their receipts without a layout runtime (`reused_layout_pages`,
  `detected_layout_pages`, `layout_status: reused`); `--redetect` runs the detector for every
  page. `redetected_override_pages` names an override page only when the fresh detection
  differs from the recorded one, and the units of a page whose receipt survived the rerun
  stay `verified` instead of reading as fresh work.
- The record names no machine: `project init` records a PDF inside the project relatively
  (`source/<name>.pdf`, also in `provenance.json`); the Cursor dry-run record's `packet_path`
  is project-relative (absolute values written earlier still import on their host) and
  `reviews/external-dry-run/**/*.json` belongs to the record; the scaffolded `tools/lt.py`
  also records the plugin root relative to the home directory and resolves the plugin through
  `LITTRANS_PLUGIN_ROOT`, the recorded root, the same path under this user's home, their
  newest siblings and each client's plugin cache (a foreign-OS path is never searched
  relative to the working directory); `tools/lt.sh` and `lt.py` are executable on POSIX;
  the scaffolded `.gitattributes` keeps `*.cmd` CRLF and `*.sh` / `*.py` LF. The layout
  cache root follows the platform (`%LOCALAPPDATA%` on Windows, `$XDG_CACHE_HOME` or
  `~/.cache` elsewhere), as the CLI environment already did. `fidelity-workflow.md` gains
  "Several hosts, one record" — the protocol for sharing a project through a git remote —
  and `runtime.md` the cache locations and environment variables per platform.
- `scripts/check.sh` runs the release checks on Linux and macOS, and the `release-checks`
  workflow runs them on `ubuntu-latest` beside `windows-latest`.

- Reference terminology is a channel of its own: `glossary/reference.yaml` (a `terms` list with
  the approved-term schema plus `kind` and `aliases`; `status` defaults to `reference-only`,
  `approved` is refused there) and `status: reference-only` entries in `approved.yaml` reach
  translate, audit and external-review packets under `# Relevant reference terminology (not
  gated)` grouped by `kind`, filtered per unit exactly like gated terms and never enforced by
  QA. The section is emitted only when an entry matches, so projects without reference entries
  keep their audit context; with them, editing an entry resets only the batches that mention
  it instead of every finished audit (the whole-file cost that `context/*.md` edits still carry).
  `aliases` select an entry under every match mode. The finalize unresolved report lists only
  undecided candidates (no `status` or `proposed`) and counts the decided ones. New read-only
  `glossary lookup` (by batch, pages, unit ids or a text file; `--kind`, `--jsonl`) and
  `glossary check` (schema errors and entries matching no prepared unit) commands.
- Audit runs record the shared context by part (`shared_context_parts`: brief, style guide,
  approved terms, reference terms with sha256 and line count); a `context-changed` stale entry
  lists `context_changes` — which part changed and its lines before and after — in
  `audit_coverage`, `workflow status` and `review status`.
- `project init` grows the record structure, not only the directories: the two context files
  open with the rule/record boundary note, `glossary/reference.yaml` joins `approved.yaml` and
  `candidates.yaml` (each headed by its effect), and the project gets a `.gitignore` that
  keeps the source PDF, `output/`, the layout cache, per-asset `original.pdf` and unreferenced
  source packets out while tracking `.littrans/work/` and ignoring the lock, plus
  `.gitattributes` (LF), `README.md`, `AGENTS.md` (`CLAUDE.md` imports it), `PLUGIN-ISSUES.md`,
  `docs/{HISTORY,DECISIONS,TERMINOLOGY,REVIEWS}.md`, a host-agnostic launcher (`tools/lt.py`
  with `lt.cmd` / `lt.sh`; resolves `LITTRANS_PLUGIN_ROOT`, the recorded plugin root or its
  highest-versioned sibling) and the plugin-owned `docs/LITTRANS.md` rendered from the package
  constants. `--repo-root DIR` places the repository-level files at an ancestor for nested
  layouts. New `project scaffold PROJECT [--refresh]` adds missing files to an existing project
  (user-owned files are never overwritten; an existing `.gitignore` only gains the
  `.littrans/*` / `!.littrans/work/` pair) and regenerates `docs/LITTRANS.md`. New
  `project tracked PROJECT` derives the record from the data (assets, receipts and the source
  packets they name, batches, ledgers, packet payloads) and asks git whether exactly that set is
  tracked, exit 1 on any gap. `project rebuild` also copies `docs/` and reports what it copied.
  `context/chapters/` is no longer created (nothing read it). `WORKFLOW_PACKET_STAGES`,
  `AUDIT_STALE_REASONS` and `DETERMINISTIC_QA_VERSION` are named constants.
- Inline notation keeps a text-face operator name set flush against its argument's bracket
  (`Cov(`, `mean(`, `area(`) like a known operator; `MATH_OPERATORS` gains `limsup`, `liminf`,
  `cov`, `var`, `corr`, `prob`, `vol` and the hyperbolic/inverse trigonometric names, so
  `limsup` is no longer declared as a formula condition.
- A text-face closing bracket is trimmed from an inline run only when the run's closers
  outnumber its openers and prose follows it (`(the space L^p(Ω))`); intervals count every
  bracket kind together and brackets at a line edge are kept.
- An inline formula TeX broke after a relation or operator (`f(λ) >` / `0`, a summation sign
  ending a block) is one asset with a fragment per line (`line-break-continued`); the digit or
  bracket opening the next line is part of it.
- A displayed formula box owns the rows a stretched delimiter it owns brackets (`0,
  otherwise.`) and no longer returns a fraction denominator (`vol(B)`) to the paragraph as a
  set-off phrase; the p33-style cases density exports complete.
- Language tokens include letter-dot abbreviations (`i.o.`, `a.s.`, `i.e.`) everywhere one
  predicate now serves: automatic conditions, condition validation and recoverable-prose
  counting. `formula_conditions` may be declared on any math asset, inline or displayed, and
  preparation declares them for inline crops and for reviewer regions that omit the key.
- An equation label sharing its PDF block with a proof tombstone (`□ (1.50)`) binds to its
  display; the tombstone stays in the reading order. Structure assembly never merges a printed
  label (`(1.50)`, `(A.4)`) into the preceding paragraph.
- The build identity (`generator`) is recorded but never fingerprinted: page ledger
  fingerprints, packet page fingerprints and packet identities exclude it, so re-preparing
  identical content keeps the packet ID and the review receipt (`retained_receipt_pages`).
  Pages prepared by the earlier 0.6.0 development build change fingerprint once.
- `source prepare --replace` replays a page's recorded reviewer override instead of silently
  re-deriving the page (`replayed_override_pages`); `--discard-overrides` re-derives
  (`discarded_override_pages`). Ledgers record `source_overrides_origin` (packet, reviewer).
- Layout results (`derived/fidelity-layout/*.json`) key their `pages` by page-image SHA-256
  and their fingerprint binds image content, weights, runtime and worker — never a path of the
  project — so a moved, cloned or restored tree finds and replays its own layout evidence
  instead of silently re-cutting pages by the fallback rules (`layout_page_items`; results
  written by earlier builds are still read by path or, once moved, by image file name).
  Pages prepared without an override by an earlier development build change
  `layout_fingerprint` (and so their page fingerprint) once when re-prepared.
- The layout store is content-addressed: `detect_layout` takes the store directory and
  writes `<fingerprint>.json` (plus `.request.json`/`.log`, `path` in the result), so a rerun
  on another runtime writes a new file instead of overwriting the result the page ledgers
  record; a whole-chapter `--replace` after an upgrade therefore replays every override page
  on its recorded result and keeps its receipt, re-detecting only the pages without one.
- A recorded layout result that `derived/fidelity-layout/` no longer holds stops an override
  import with the missing fingerprint and the page to re-detect, instead of re-preparing the
  reviewed page as `unavailable`; `source prepare --replace` replays such a page on the fresh
  detection and lists it in `redetected_override_pages`.
- A printed list label opening a line — a closed number (`1.`, `1.11.`, `3.2.1.`, `2)`) or a
  bracketed clause marker (`(a)`, `(iv)`, `(2)`) set in a text face and followed by text on
  the same line — is a structure boundary. Preparation cuts a chunk at each label it can
  place geometrically (it opens its block, follows a label, opens right of the running text
  or of the open item's label, or its text column is an open item's), keeps the lines aligned
  with an item's text column as that item's continuation whatever the page's dominant
  line-start margin is, and records the decisions in the page ledger's
  `structure.list_items` (`{"label", "body_x"}` per label chunk, `{"continues"}` per
  continuation chunk; the key is absent on pages without labels, whose ledgers stay
  unchanged). Structure assembly opens a parent group at every numbered label unless a
  statement is open (its items join the statement like bracketed clauses do), never merges
  a label unit into the preceding unit, and treats a continuation chunk as the item's text
  rather than an indented paragraph. A word hyphenated across the seam of two merged
  fragments follows the same document evidence as a line end inside a block. Exercises
  numbered `1.11.` therefore become one unit each with their clauses and displays as
  children; pages carrying labelled lines change fingerprint on their next `--replace`
  preparation (chapter-1 clause lists keep their units; the Exercises pages change units).
- A `preserve_asset_id` region on a `math` asset without a declaration is declared
  automatically from the asset's own glyphs, like a region naming the same glyphs (provenance
  gains `auto-formula-conditions`), so a preserved crop no longer needs a hand-written copy of
  the conditions to pass `undeclared-formula-language`; an existing declaration is kept and an
  explicit list is the reviewer's. `fidelity-workflow.md` gains an "Asset identity" section:
  export identity (`evidence.json`), the creation-time directory name and default ID (folded
  with the conditions declared at creation), `content_sha256`, and the preserved asset's
  frozen ID and directory.
- Review imports and preparation report pages outside the operation whose receipt depended on
  a changed page (`invalidated_pages`) and remove that receipt explicitly.
- The approval gate and the checkpoint attention list share `page_review_findings`: an asset
  with a pending grouping decision blocks approval unless the decision lists it in
  `accepted_grouping_pending` with a reason; language a math crop holds without a declaration
  (`undeclared-formula-language`) blocks approval; receipts record `failures` and imports
  return `rejected_pages`. `review-template.json` pages carry a `context` block (declared
  formula conditions, pending grouping assets, boundary diagnostics, findings). Existing
  approved receipts of pages with unaccepted pending grouping decisions fail verification
  until re-reviewed.
- A receipt whose packet directory is missing fails with a message naming the packet as a
  live review dependency; `source verify` reports `receipt_packets` and `source gc` reports
  `live_source_packets`/`unreferenced_source_packets` without deleting packets.
- `littrans doctor` prints the installed `build` (`plugin_version`, `build_digest`,
  `package_path`).
- QA v6.15 folds glossary sources and unit source text alike before matching (TeX spacing and
  combining accents, ligatures, curly quotes, dash variants, whitespace, case), so `Hölder`,
  `Lévy's` or `Chebyshev's` gate the extracted `H¨older`, `L´evy’s` and `Chebyshev’s`. QA and
  packet term injection share one `term_source_text`, including quoted-title removal. Entries
  accept `match: substring|word|regex`; only `status: approved` (or absent) entries are enforced;
  a source that matches no prepared unit is reported as the `approved-term-never-matched`
  warning. `fidelity-workflow.md` documents the entry contract, including that `forbidden`
  applies to every unit.
- QA v6.16 folds the literal characters of `match: regex` glossary sources like substring
  sources while keeping escape sequences verbatim, so `Hölder` or `Chebyshev’s` written in a
  pattern no longer fails silently against the folded source text.
- An `equation` unit without asset placeholders whose text is native words (`Prob`,
  `otherwise.`) renders as upright text — and shows its translation — in packets, Markdown and
  bilingual HTML instead of a spaced, slanted MathML symbol sequence.
- Displayed blocks that carry native prose keep their rows (`\n` in `source_text`); the HTML
  editions stack them as `display-row` spans with a leading asset (a stretched brace) as a
  column beside them, Markdown emits hard breaks, inline fragments coalesce along a row only,
  and QA warns `display-rows-mismatch` when the translation's row count differs.
- Control characters in a mathematical face are ink: the CMEX integral (CR) and big
  parentheses (LF) are owned by their display region instead of being cut out as a separate
  asset bound to the following unit. The review packet reports `math-ink-outside-ownership`
  for symbol-face ink inside a displayed crop owned by another asset.
- Words kept inside a displayed formula (`if`, `otherwise.`, `for all`, `is even`) are declared
  automatically as `formula_conditions` (provenance `auto-formula-conditions`), making the unit
  translatable and requiring an image-language companion; operator names applied to their
  argument (`Prob(`) are not conditions. Condition `source_text` is compared ignoring
  whitespace, so TeX word gaps may be written as spaces.
- Pieces of a stretched delimiter set on several baselines (⎧ ⎪ ⎨ ⎪ ⎩) stay in one region
  (`stretched-delimiter-merged`) instead of splitting the brace between the display asset and
  the native runs of its rows.
- A region override's `bbox` is the target box: only owned glyph ink is padded, so a
  `fragment.bbox` echoed from the packet reproduces the asset (`bbox`, `width`, `height`,
  `baseline`, `content_sha256`) unchanged; fragment dimensions derive from the rounded box.
  Raw regions without owned glyphs lose their extra 0.5pt padding, which changes their
  identities on the next `--replace` preparation.
- The source checkpoint's attention list groups pending grouping decisions per page with the
  asset IDs collapsed in `<details>`, instead of one line per asset.
- Original glyph paths are measured and exported through the page-sized clip group MuPDF emits
  when a PDF CropBox differs from its MediaBox; such pages no longer degrade every asset to a
  nominal-box `raw-region` crop that truncates stretched delimiters. Regions whose glyph ink
  could not be measured record `ink-bounds-unmeasured`.
- Line-end hyphens are rejoined only when the document does not print the compound more often
  than the joined word (`well-` / `known` stays `well-known`); suspended hyphens inside a line are
  left alone. Re-preparing affected pages with `--replace` changes those units' text and
  invalidates their translations.
- Preparation keeps footnote links on the prose chunk that still carries the call when a
  native block is split around a display formula, and rejects an inconsistent footnote graph
  inside the source transaction instead of publishing units that no packet can review.
- QA v6.14 reports `empty-translation` when source prose beside `{{asset:ID}}` placeholders
  has no target text; only asset-only source blocks may translate to placeholders alone.
- A resubmission that changes only `image_evidence` updates the receipt in place; the record
  keeps its revision/status and audit coverage, while the receipt-bound QA context goes stale.
- `fidelity-workflow.md` is a structured reference (states, preparation, review decisions and
  the full source `override` contract, batches, records, assets, QA, audits, coordination,
  rendering, recovery) instead of an appended change log; the packet stage list includes
  `source-review` and `revise`. The plugin README lists `batch create`/`batch refresh`, and
  the translation-record example uses real evidence image paths.
- `batch create --unit-ids` and `assets packet --asset-ids` trim whitespace and empty entries.

- Removed the orphaned math-review, math-vision, math-packet, math-pilot-packet and
  schema-migration modules together with their tests; their CLI commands and JSON schemas
  were already retired. Existing `math_review_decision_id` layout overrides still apply.
- QA v6.13 binds current required images and translation viewing receipts, including
  dependent units; changed image bytes invalidate cached passes without semantic changes.
- Share dollar-math boundaries across source, QA and HTML so ordinary currency amounts
  do not hide real footnote calls; preserve explicit display math.

- QA v6.12, Markdown and HTML share line-valid code-fence boundaries, including longer
  closing fences and unclosed blocks. Inline code uses exact backtick runs.
- Retry incomplete managed model downloads when READY is absent; handle whitespace-only
  PDF lines before bold run-in labels without indexing an empty glyph list.

- Freeze explicit/untranslated-only batch scopes across refreshes, and reject newly cut
  logical groups. Keep footnote companions inside Markdown definitions.
- Include shared MathJax files in edition rollback and publish each runtime file atomically.

- Revalidate source authority during workflow coordination and dispatch source-review
  recovery when receipts fail. Route read-only review issues to editable owning batches.
- Emit image companions after complete continuation chains; select current dependency
  evidence before choosing a formal render cover. Require Pydantic 2.12 for identity serialization.

- QA v6.11 rejects live footnote calls in image-language companions, including table cells
  and both label fields; escaped and code-literal notation remains supported.

- QA v6.10 binds dependency presence and source identities into cached results; new
  untranslated-only batches retain stale records as editable work.
- Repair interrupted candidate/review index publication without replacing newer valid
  evidence. Recovery packets isolate damaged candidates and preserve valid revision context.
- Publish and roll back source page canvases atomically; reject malformed layout predictions
  and asset placeholders in image-language companions.

- QA v6.9 rejects missing or stale dependency translations; refreshing a batch reopens
  stale translated read-only units. Currency escapes no longer swallow footnote calls.
- Source-review layout cache reuse requires a list-valued result for the current page.

- Skip unreadable unrelated layout caches during source-review overrides, and retain
  every fragment's image-language companions when rendering continued tables.

- Publish isolated layout-worker results atomically and recompute unreadable caches;
  reject incomplete worker results. HTML protects multiline backtick and tilde fences.

- Render asset-bearing target tables once in Markdown and bilingual HTML, preserving
  explicit empty target text. QA v6.8 ignores tilde-fenced literal footnote syntax.

- Recover damaged candidate evidence through fresh transcription packets; schedule assets
  across the same dependency closure as QA.
- QA v6.7 counts real footnote calls with multiplicity while excluding literal syntax.
- Retire translations of removed source units transactionally, and defer approvals made
  stale by same-import overrides. Preserve source table structure around asset cells.

- Corrupt indexed asset reviews require renewed verification and fresh packet identities.
- Schedule uncertain fallback recovery before QA; QA v6.6 checks asset uncertainty across
  all dependency units, including non-translatable formulas, and checks table-cell references.
- Regenerate incomplete original asset caches when their evidence receipt is absent.

- Keep reviewer semantic uncertainty across recovery candidates until a valid independent
  audit supersedes it; dispatch recovery audits before QA and retain the block if review
  evidence is damaged. Older recovery candidates inherit their recorded review context.

- Validate explicit footnote numbers against unique referenced definitions, preserving
  literal code/math syntax and repeated calls to one definition.
- Expose recovery transcription for uncertain fallback assets, carrying review feedback;
  QA blocked only by that uncertainty dispatches asset recovery instead of prose revision.
- Restore source authority snapshots on KeyboardInterrupt as well as ordinary errors.

- Generate batch output schemas from TranslationRecord, including image receipts and
  asset companions; refreshing a batch updates its emitted schema.
- Bind workflow packet identity and manifest to the selected host, model and effort.
- Validate source override footnote relationships against the final combined graph,
  rolling back invalid references before publishing source units.

- Keep later asset-review decisions authoritative when an older review is replayed;
  replay only restores an absent index entry or retains its existing mapping.

- Protect escaped footnote literals and backslash-delimited math in source/bilingual
  HTML; embed original PNG fallbacks alongside SVGs in standalone source checkpoints.

- Dispatch dependency-only QA revision work to an editable owning batch, including
  prerequisites outside a resumed wave; status uses the same dispatch and explicit host.
- Keep table-cell footnote calls in QA and prevent image companions from satisfying
  prose preservation checks. QA context version 6.5 requires rerunning existing QA.
- Bind asset kind, display and grouping semantics into versioned content identities,
  including final structure assembly, so source changes invalidate translation evidence.
- Retain complete groups in untranslated-only batches with explicit read-only context
  units excluded from submission, including after batch refresh.

- Validate explicit asset IDs before either single- or multi-fragment region export.
- Recognize spaced, punctuated and Unicode footnote-definition labels in detected regions.
- Preserve historical rights status when rebuilding a project.
- Serialize structure-profile extensions with the project write lock.
- Route current failed deterministic QA to revision; missing/stale QA still runs first.

- Reject unsafe override unit IDs and escape legacy IDs in Markdown anchors.
- Preserve footnote-like literals inside backslash-delimited inline/display math.
- Prepare numeric/symbol-only pages using usable-glyph font sizes or a default.

- Carry explicit host selection into asset transcription/audit packets and the asset CLI.
- Emit unique Markdown footnote calls/definitions, preserving code literals and expanded
  note content; keep continued table fragments with scoped notes separate.
- Reject source-unit ID collisions across pages and within a source-review import.

- Bind source coverage HTML and its original-page images to review packets and receipts;
  damaged reports require new packet identities and fresh visual review.
- Resolve cross-page footnotes through source unit references, including page-scoped output;
  recognize Computer Modern Roman note calls while excluding mathematical bases.
- Support explicit coordination hosts in workflow packet creation and reject source override
  asset ID collisions before replacing reviewed page data.

- Bind layout caches to worker code, interpreter identity and installed package versions.
- Validate source-review receipt digests, packet provenance and visual approval conditions
  whenever source approval is consumed; legacy receipts require fresh review.
- Preserve page and language footnote scopes inside table cells.

- Enforce managed layout readiness before detector execution or cached evidence reuse.
- Verify stored asset-review digests before consuming decisions or replaying imports; require
  the manifest SHA-256 receipt in the public submission schema.

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
- Added Qoder as a supported coordinator host: `.qoder-plugin` manifest and marketplace, `QODER_*`
  host detection with 3/6 waves, `--host qoder`, reused tool-restricted read-only reviewer agents
  and host documentation. `agent_models.qoder` ships empty for explicit per-project configuration;
  Qoder-hosted external review remains a later revision.
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
- Added `workflow packet --stage revise`: the translate packet files plus the batch's current
  translation records, its open review issues and revision instructions, so one fresh translator
  can consolidate an audit round; `revise` tasks use the translate model policy.
- Reported why audit coverage is stale: `audit_coverage`, `review status` and
  `workflow next|status` now carry `stale`/`stale_reasons`/`audit_stale` (`context-changed`,
  `dependency-changed`, `unit-changed`, `invalidated`, `closure-incomplete`,
  `context-units-removed`); audit runs record the shared brief/style/term fingerprint separately.
- `review import-set` keeps the reviewer's own id as `source_issue_id` next to the canonical
  `audit-<hash>` id; `review resolve` accepts either id and several comma-separated ids at once,
  and `review issues PROJECT BATCH [--all] [--jsonl]` lists a batch's issues.
- Allowed a packet or render batch set to mix batch series when their units do not overlap and
  source order holds; batches within one series must still be consecutive.
- `render` switches to originals-only automatically when the project holds no transcription
  candidate and records `originals_only_reason` in the render QA and command output.
- The rendered edition's header, `*.quality.md` (now listing its batches and translation status)
  and `render-qa.json` (`rendered_status`, `review_batch_ids`) describe the rendered batches, not
  the project-wide status; QA report counts are scoped to those batches.
- Added deterministic QA warnings `target-halfwidth-punctuation` (half-width `,.;:!?` after
  Chinese text) and `asset-reference-spacing` (whitespace between Chinese text and
  `{{asset:ID}}`); the QA context fingerprint is now `v6.4`, so existing batches report stage
  `qa` until `qa run` is rerun (audit coverage is unaffected).
- Writer, audit and revise packets carry a "Contracts" paragraph (renderer-owned list/heading/
  note markers, placeholder spacing, full-width punctuation, `language_present=false` with notes)
  so reviewers stop reporting the contract as defects; the `asset-language-untranslated` message
  names the notation-only alternative.
- The CLI reconfigures stdout and stderr to UTF-8 with LF line endings, so piped output on a GBK
  Windows console needs no `PYTHONIOENCODING` and carries no carriage returns; generated batch, context and
  schema files are written with LF. PyMuPDF is imported as `pymupdf`, so its `fitz` deprecation
  notice no longer lands in the CLI's stdout.
- A packet's `model` and `reasoning_effort` are dispatch values: what the coordinator hands to
  the host's task launcher (`agent_models.<host>`, an alias such as `sonnet` on Claude Code or a
  concrete id), never a claim about the model the host served. `assets submit` still requires
  the submission to echo them, but says so; a new optional `served_model_label` records the
  model the writer's environment reported, verbatim and unverified, next to `model` in the
  candidate record. The submission, packet-manifest and project schemas describe the fields.
- External reviewers (and their fallbacks) take an optional `model_identity`: the concrete id
  host metadata must report when the configured `model` is a host alias routed to another model.
  Verification compares host evidence with the identity (or with `model` when unset); a failed
  verification names requested, expected and served models, and the failed run and attempt
  keep the served label (`actual_model_label`, `actual_model`) with `model_verified: false`.
- The CLI reports a refused precondition (a stale audit packet, a missing batch, an invalid
  submission, a path that does not exist) as its error message with exit code 1 instead of a
  traceback; the stale audit packet messages say what changed and that the packet must be
  rebuilt and re-reviewed.

### Fixed

- Bound asset-review artifacts to every copied original and MathJax dependency; missing or
  modified dependencies require a fresh audit packet and review, including for earlier approvals.
- Restricted structured candidates to math/LaTeX, table/table and code/code. Figures and
  unclassified mixed regions retain originals until source review establishes a supported kind.
- Preserved reviewed tables and code in Markdown with original-image references.
- Sanitized source-checkpoint output names and made rebuilt projects own a portable source copy.
- Exposed optional asset work after reading completion and recovered multi-digit footnote calls.
- Required successful smoke-test readiness for managed layout runtimes and made failed installs retryable.

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
- Kept a displayed line that carries prose beside its formula (`... for all times t > 0.`)
  translatable when the unit was rebuilt from a formula-only block; the quantifier phrase was
  being dropped from the translation as a non-translatable image.
- Labelled non-translatable equation units explicitly in audit packets so reviewers do not report
  the original-image reading content as an omission.
- Kept inline formulas that fell back to a raw mixed region inline in the reading edition instead
  of forcing block display and breaking the sentence.
- Paragraph white space is a structure boundary. A document that spaces its paragraphs instead
  of indenting them had every flush block of a page merged into one unit (the whole body of a
  page as one `paragraph`, with its detector-labelled reference lines absorbed) because the
  planner's gap rule never crossed PDF blocks and assembly re-merged every flush chunk of one
  group. Preparation now flags a chunk that opens after white space wider than the page's
  paragraph gap when the text line above it closes (ends in terminal punctuation or stops
  short of the running text's right edge); assembly treats the flag like a paragraph indent —
  a new group, never merged into the unit before it, closing an inferred statement unless the
  prose resumes after the statement's enumerated clauses. A line a tall inline formula pushed
  down, a formula row, a label or a tombstone beside a display never counts, so indented books
  keep their units and page fingerprints. `footnote` and `bibliography` units open their own
  group and never merge with prose (wrapped fragments of one footnote still merge).
- A rejected page no longer reads as verified after a rerun (0.6.0-dev.17). `source prepare
  --replace` keeps the receipt of a page it reproduced byte for byte, and marked that page's
  units verified whatever the receipt decided, so re-preparing a page whose visual review had
  *failed* flipped its units to `verified` in `derived/units.jsonl` and in the source packet a
  translator reads. Only a receipt that passed now says the page is verified; a retained
  rejection leaves it unverified, as the review import left it. `verify_fidelity` always
  refused such a page, so no page was ever approved on this — the record simply disagreed
  with the gate.
- Declared language is judged on the same geometry that declared it (0.6.0-dev.17). The
  approval gate re-derived a math crop's formula conditions from the ledger, which records PDF
  font-metric boxes, while preparation derives them from measured glyph ink. A stretched CMEX
  delimiter's metric rectangle sits on an adjacent line, so an upright operator name applied to
  a `\left(` argument (`Prob(`, `vol(`) read as notation when it was declared and as undeclared
  language when it was checked, and the page could not be approved or re-prepared out of it.
  The check now reads the ledger in its own metric-box terms and keeps a bare operator name
  notation there too; what preparation declares is unchanged, so no page fingerprint moves.
- Reclaimed crop directories while the project write lock is still held (0.6.0-dev.17).
  `source prepare` and `source import-review` pruned the asset directories no fragment refers
  to after releasing the lock, using their own in-memory registry: a second run that acquired
  the lock in that window and exported new crops could have them deleted, leaving its
  `derived/fidelity-assets.jsonl` pointing at missing files. The authority transactions now
  commit on a nested stack inside the lock, so the prune still runs after the new record is
  durable but before another run can start.
- `.gitignore` upgrades no longer leave the packet payloads outside the record
  (0.6.0-dev.17). Adding the `.littrans/*` / `!.littrans/work/` pair to a project created
  before it left the older `/.littrans/` line in place; git never descends into an excluded
  directory, so the re-include could not take effect and `project tracked` reported every
  packet payload as excluded from the record. The whole-directory line is now removed when the
  pair is added.
- Replaying a page of corrections renders the page once (0.6.0-dev.17). Declaring the language
  of a reviewer's math regions re-rendered and re-parsed the whole page SVG for each region;
  it now reuses the ink the page already measured.

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
