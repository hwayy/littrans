# Rebuilding a project for LitTrans 0.6

Version 0.6 uses schema 6 and a single fidelity-preservation workflow. Keep older projects intact; create a new project rather than upgrading extracted units or inheriting verification receipts.

```text
littrans project rebuild OLD NEW
```

`NEW` must be a new destination. Rebuild copies the source PDF, document brief, style guide and glossary. Old extraction, translations, batches, source verification, asset decisions, QA and approvals remain historical in `OLD`; none is treated as current v6 evidence. Confirm the new source fingerprint and copied context before preparing pages. Review role/external-service configuration explicitly where needed rather than inferring current credentials from historical output.

Run `source prepare`, inspect `source review-packets`, import source-bound visual review and run `source verify`. Create new batches, then dispatch independent transcribe and translate packets. A faithful original-image formula is ready for reading and translation while its structured candidate remains pending.

Older project schemas receive a rebuild instruction on write operations. Resume old work only with its recorded compatible plugin installation in a separate task; do not mix versions against one project. Checkpoint running tasks before changing the installed plugin, and retain caches they still use.

For QASC, keep the existing project as history and prepare an independent v6 project from its source/context. Experimental gold answers and historical model candidates are evaluation material, not input for new production workers.

After interruption, use status on the frozen new batch IDs, recover saved successful responses, import them idempotently and schedule only missing work. A pending LaTeX candidate does not reset a completed translation; a changed source or semantic dependency does require current review.

## Projects prepared with 0.6.1-dev.7 or earlier: script spaces, `!` and list ends (0.6.1-dev.8)

- **Nothing changes until a page is re-prepared, and every existing receipt keeps passing.**
  Recorded pages, assets, overrides and review findings stay as they are.
- **On re-preparation these forms cut differently:**
  - the word space after an inline asset that ends in a superscript or subscript
    (`{{C^∞}} in`, `{{X^∗}} is`) is read from the printed ink gap (at least a quarter em at the
    prose's size), where the text layer used to drop it; a hyphen after a script (`F_t-adapted`)
    stays joined. Unit text changes, asset IDs do not;
  - a text-face `!` that closes an inline formula leaves the crop when the block ends with it
    or a capital follows (`I(f)!`); a factorial (`n! ways`, `n!,`, `n!.`) keeps it;
  - prose that returns more than half an em left of a hanging labelled list's labels after a
    closed sentence and opens with a capital is a unit of its own instead of the tail of the
    last item's text (`(iii) … . It can be shown …`). Its parent is unchanged, the list's
    introducing paragraph, as for such prose in a block of its own.
  Measured on a 212-page book with the recorded detector results, prepared natively: 12 pages
  change (8 restore a word space after a script, 1 returns a `!` to the prose, 3 split prose
  from a list's last item), no asset ID moves except the one whose `!` left it, and replaying
  the book's recorded overrides changes no page's fingerprint; a 7-page pilot is unchanged
  either way. A page whose
  recorded `units` override covers one of these forms replays as recorded; re-prepare it with
  `--discard-overrides` only if the new native reading is wanted.
- **Not changed (by decision):** a letter-dot abbreviation set in the mathematical face
  (`$A_n\ i.o.$`, `a.s.`, `i.i.d.`) is notation the formula image carries; it is not declared as
  a formula condition, and the unit stays untranslated like any pure formula.
- **Not changed (documented limitations):** a display block that holds several labels beside
  one formula keeps the extra labels in its text (`equation_number` holds one number); where
  prose after a list belongs (the introducing paragraph's group, or a group of its own) and
  containers spanning pages or several upright paragraphs (Example, Step algorithms, proofs
  continued on the next page) stay `units` override decisions; a factorial `!` at the very end
  of a block, or followed by a capital, reads as the sentence's. A capitalised sentence that
  continues across a page edge (`in the space of` / `Markov chains`) needs no override: the
  sender's `continued_to_next` joins it in rendering, batching and audit closure.

## Projects scaffolded with 0.6.1-dev.2 or earlier: launcher priority (0.6.1-dev.4)

The generated `tools/lt.py` is project-owned, so `project scaffold --refresh` does not replace
an existing copy. To adopt the corrected priority, back up `PROJECT/tools/lt.py` outside the
project record, remove the original, then run the upgraded installed plugin's
`littrans project scaffold PROJECT`. Pass `--repo-root DIR` for a nested project. This
recreates only the missing launcher and other missing scaffold files; it does not overwrite
the project's remaining files. Review and reapply any local launcher customizations from
the backup. Until then, set
`LITTRANS_PLUGIN_ROOT` to the desired installed plugin directory to override the old launcher.

## Projects prepared with 0.6.1-dev.1 or earlier: typographic kinds, list margins and per-unit structure checks (0.6.1-dev.2)

- **Nothing changes until a page is re-prepared, and every existing receipt keeps passing.**
  Recorded pages, assets and overrides stay valid; a receipt bound to a packet made before this
  version is verified as before, without the new confirmation lists.
- **No asset changes.** Re-preparation changes only unit kinds, paragraph seams, parents and
  page-edge flags, and only on these page forms:
  - a chunk a detector labelled as a heading or caption but set like running text becomes a
    `paragraph` (italic step lines, run-in theorem lines, "Figure N shows …" sentences); the
    overruled label is recorded as `structure.overruled_labels`, which also moves the page's
    fingerprint when the chunk is replayed from a `units` override;
  - a page whose most common line start was a list's text column gets its prose margin back:
    paragraph indents between the margin and the labels open paragraphs again, lead-ins stop
    merging into the paragraph before them, and page-top continuations are flagged. The
    ledger's `structure.margin` (and `first_x`/`indent_style` readings) moves even where the
    units do not;
  - a paragraph indented inside a list item is a unit of its own in the list's container.
  A page corrected by hand for any of these replays its recorded `units` as recorded; re-prepare
  it with `--discard-overrides` only if the new native reading is wanted.
- **New packets need per-unit confirmations.** `source review-packets` now lists
  `context.structure_checks` for every page, and a decision must copy each `roles` row into
  `confirmed_roles` (`{"unit_id", "kind"}`, the kind read on the original) and each `joins` row
  into `confirmed_joins` (`{"block"}`). Scripts or agents that fill review templates must be
  updated: a decision that only sets the page flags is rejected with `role-unconfirmed` /
  `join-unconfirmed`. Correct a disputed kind with a `units` override instead of confirming it.
- Lists stay flat: the lead-in is the parent of the items and of the displays and paragraphs
  inside them. A project that hangs a display from its item keeps that as a `units` override.

## Projects on 0.6.0-dev.15 or earlier: the per-role dispatch policy (0.6.0-dev.16)

- **`project.yaml` needs no edit.** The old flat `agent_models.<host>` (a model string per role
  plus one shared `reasoning_effort`) is still read and normalized; the shared effort becomes the
  default of every role that states none. The file is rewritten in the nested per-role form the
  next time the project is saved. Give a role its own effort by writing it out:
  `translate: {model: sonnet, reasoning_effort: high}`.
- **A misspelled host or role key now fails loudly** instead of leaving that host unconfigured.
  The supported roles are `translate`, `transcribe`, `audit` and `asset-audit`.
- **Finish or discard in-flight packets before upgrading.** A translate, revise or asset-audit
  packet's identity covers its dispatch policy and its files, and both change here: the
  translate context packet now carries only that packet's own resolved dispatch rather than the
  whole four-host map, and an `asset-audit` packet takes the `asset-audit` role's effort instead
  of the host-wide one. Packets are recreated under new ids, so a worker returning with an old
  `packet_id` fails its binding. Submitted work, translations and approvals are unaffected.
- **Nothing is blocked by an unset model any more.** Cursor and Qoder projects that could not
  create translate or transcribe packets now create them with `model: null` and dispatch on the
  host's own policy. Run `project models PROJECT --host HOST` to see the resolved policy and any
  advisory.

## Projects prepared with a 0.6.0 development build

- `context/chapters/` is no longer created; nothing ever read it, and an existing directory is
  harmless.
- Run `project scaffold PROJECT` (with `--repo-root DIR` for a nested layout) to add the record
  files a project created earlier lacks — `glossary/reference.yaml`, the handbook, records,
  ledger, launcher and `docs/LITTRANS.md`. Existing files are never overwritten; an existing
  `.gitignore` only gains `.littrans/*` and `!.littrans/work/`. Run it again with `--refresh`
  after every upgrade so `docs/LITTRANS.md` describes the installed build.
- `glossary/reference.yaml` is now read: it must hold a `terms` list (the same entry schema as
  `approved.yaml`, plus `kind` and `aliases`). A file in another shape fails to load with a
  clear error; convert it before creating packets. Adding reference entries changes the audit
  context of the batches whose units mention them, once.
- Paragraph white space now ends a paragraph (0.6.0-dev.8). A document that spaces its
  paragraphs instead of indenting them — reports, papers, anything whose flush blocks were
  merged into one unit per page — must be re-prepared with `source prepare --replace` and its
  pages reviewed again; the new units carry new IDs and no translation of the merged unit is
  reused. An indented book changes only on a page where prose opens after real paragraph
  white space (a theorem's conclusion after its enumerated clauses, say); every other page
  keeps its units, fingerprint and receipt. Detector-labelled `footnote` and `reference`
  blocks that were absorbed into prose become units of their own kind on re-preparation.

## Projects prepared with 0.6.0-dev.14 or earlier: script digits, table headers and dependency receipts (0.6.0-dev.15)

- **Nothing changes until a page is re-prepared.** Recorded pages, assets, receipts and
  overrides stay valid; `source verify` reads as before.
- **On re-preparation (`source prepare --replace`, or an override import) these forms change
  the page's units:** a power of a text-face number (`2^{19937} − 1`, `10^6`) becomes one
  inline math asset instead of flattened digits and a fragment (`219937 {{asset}}`), so the
  paragraph's text and the asset's ID move; a detector table whose column headings sat
  above its box becomes one table asset holding headings, rules and rows, so the block that
  carried the headings as prose becomes a `table` unit (its text is the placeholder alone)
  and the asset's ID moves. A page corrected by hand for either form (a `regions` override
  cutting the power or the whole table) replays as recorded.
- **`import` and `source prepare --replace` now name every receipt they remove.** A page a
  correction reaches only through the edge it adds — units re-parented to a container on
  the page before — appears in `invalidated_pages` and loses its receipt at import time.
  A project that ran a full-range `source verify` after each import to find such pages may
  stop; a receipt such a verify already found stale is already gone.

## Projects prepared with 0.6.0-dev.13 or earlier: display rows, containers and the launcher (0.6.0-dev.14)

- **Nothing changes until a page is re-prepared.** Recorded pages, assets, receipts and
  overrides stay valid; `source verify` reads as before. Workflow coordination and the
  scaffold change without re-preparation (below).
- **On re-preparation (`source prepare --replace`, or an override import) these forms change
  the page's units:** a display whose numerator, denominator or case row holds words
  (`surface area(U)` over a fraction bar, `X is discrete-valued,` inside a brace) is one asset
  with the words declared as formula conditions, so that asset's ID and the prose chunk beside
  it move; an enumerated item at a paragraph indent takes the introducing paragraph, statement
  or proof as `parent_id` (no unit ID moves); an indented italic paragraph after an italic
  statement keeps the statement's `parent_id`; a chunk the planner read as an item's
  continuation returns to the item; a proof tombstone that shared its block with a display's
  label follows the display as a unit of its own (the paragraph before the display loses the
  `□`); the first paragraph after a running-head rule is its own parent instead of the rule's
  child; and a flush page-top paragraph that opens with a capital letter no longer carries
  `continues_from_previous`. A page whose reading these forms do not touch keeps its units,
  fingerprint and receipt; a page corrected by hand for one of them (an `override.units`
  that re-parents an item or moves a tombstone) replays as recorded.
- **`workflow status` and `workflow next` no longer refuse a project whose later chapters
  are extracted but not batched.** The coverage check is scoped to the coordinated batches'
  pages and the span between them; the pages outside that scope with unbatched units are
  reported as `unbatched_pages` in both results. Nothing to do; a coordinator that batched
  every chapter only to query progress may stop doing so.
- **The scaffolded launcher (`tools/lt.py`) is user-owned and is not rewritten.** To get the
  client-aware resolution (the session's client first, Claude Code's install record, build
  metadata in version order), delete `tools/lt.py` and run `project scaffold PROJECT` (with
  `--repo-root DIR` for a nested layout), then commit it; a launcher a project patched by hand
  for the same problem may stay.

## Projects prepared with 0.6.0-dev.12 or earlier: override refusal and enumerated siblings (0.6.0-dev.13)

- **Nothing changes until a page is re-prepared.** Recorded pages, assets, receipts and
  overrides stay valid; `source verify` reads as before.
- **A recorded `units` override whose asset ID moved is now refused, not crashed on.** A
  `source prepare --replace` over a range that includes such a page used to stop with a bare
  `KeyError` naming neither the page nor the asset; it now stops with `page N: the recorded
  source override cannot be replayed (unit overrides must reference each page asset exactly
  once; not cut on this page any more: <old id>; cut but unreferenced: <new id>); re-import its
  review file or rerun with --discard-overrides`. The transaction still rolls back whole, as
  documented: re-prepare a reviewed page only when its reading changes, and for a page whose
  override no longer applies either re-import the review with the new ID (the `cut but
  unreferenced` one) or pass `--discard-overrides` for that page alone. `source import-review`
  refuses a review file with a stale reference the same way, with the page named.
- **On re-preparation, an enumerated list whose first item is followed by its own indented
  continuation paragraph changes the later items' `parent_id`** (they now name the paragraph
  that introduces the list, as the first item does); nothing else on the page moves. A page
  corrected by hand for this (an `override.units` that re-parents the item) replays as recorded.

## Projects prepared with 0.6.0-dev.11 or earlier: boundary spaces, labels and the two unit channels (0.6.0-dev.12)

- **Nothing changes until a page is re-prepared.** Recorded pages, assets, receipts and
  overrides stay valid; `source verify` reads as before. The renderer and the scaffold change
  without re-preparation (below).
- **On re-preparation (`source prepare --replace`, or an override import) these forms change
  the page's units and fingerprint:** a space at an inline-asset boundary that the printed ink
  contradicts (`{{F}} .` becomes `{{F}}.`, `{{Φ}}the` becomes `{{Φ}} the`; the text changes,
  the asset IDs do not); a printed equation label that was cut into a formula or left in a
  paragraph (`(3.11)` binds as `equation_number`, the crop loses the label's ink, so that
  asset's ID moves); one detector box over two labelled displays (now two `equation` units
  with `-displaypartN` IDs of their own); a sentence split at a tall operator or at the
  limits of an inline sum (the pieces are one unit again, and a mid-row line inside a list
  item is recorded as the item's continuation in `structure.list_items`); a hyphenated line
  end whose halves the document prints on their own (`finite-state`), a capitalised second
  half (`Fokker-Planck`), a suspended hyphen or a `{{σ}}-algebra`; a
  math-face comma before a line break, a text-face accent over a mathematical base
  (`{{θ̄}}`, one asset instead of `¯{{θ}}`), bold citation keys that were cut as variables
  (`[**KP92**, …]` stays prose), `mod` joining its operands, a connective between two formulas
  of one display kept in the display; a display block MuPDF split on one baseline (its `\n`
  becomes a space); a `figure`/`table` unit from a placeholder-only block (its `bbox` grows to
  the figure); a first body unit opening with a run-in label, statement, `Proof` or list label
  (`continues_from_previous` cleared); an item after paragraph white space (its `parent_id`
  now names the introducing paragraph); a chunk whose parent was merged away (`parent_id`
  remapped to the survivor). Measured on the pilot's pages 52–110 (chapters 2–4) with the
  recorded detector results: 18 of 58 pages keep every unit and hash, asset IDs move on 22
  (a label's ink leaving a crop, an accent or a comma joining one, a display MuPDF had split
  that is one asset again), unit IDs change on 8 (seams merged, displays cut apart), and the
  rest change text or a flag only.
  A page whose recorded `units` override references an asset whose ID moves fails its replay
  (`unit overrides must reference each page asset exactly once`): re-import the review with
  the new IDs or `--discard-overrides` for that page. Re-prepare a reviewed chapter only for
  the pages that need the fix, and review those from fresh packets.
- **Override `units` now hash like the pipeline's.** A page re-prepared from a recorded
  `units` override records the hash the pipeline would record for the same content, so such a
  page changes its fingerprint once (and needs a fresh receipt) even when its text is
  unchanged. Inline fragments are coalesced in the override channel too: an override written
  against the coalesced asset (one ID for `(8.50)`) replays, and one written against its
  constituents replays as well — a coalesced asset the override does not reference is
  restored to its constituents, so neither recording style is refused.
- **Crops:** the explicit export now keeps a rule inside the fragment's box whatever its
  distance from a glyph box, and no longer fails a page's precise export on a degenerate
  path. A crop is exported once per export identity, so an asset whose glyphs and box are
  unchanged keeps the crop it has (a table whose lower rule was missing keeps missing it);
  only a fragment whose identity moves — or a project whose crop directories were removed
  (`source gc` never removes live ones; delete the directory by hand and re-prepare) — is
  re-exported.
- **Rendering changes without re-preparation:** a paragraph whose sender page recorded
  `continued_to_next` is merged across the page edge in Markdown and HTML even when the
  receiver's `continues_from_previous` is unset, and a receiver whose sender ends in terminal
  punctuation is no longer glued to it. Re-render an edition to pick this up; no record
  changes.
- **`project scaffold`** no longer appends a second `.littrans/*` pair to a `.gitignore` that
  already carries it as `/.littrans/*`; a file that received the duplicate can drop either
  copy.
- **Not changed (documented limitations):** a sentence-ending `!` in a text face stays in
  the inline run (a factorial is set in the same face); a display block that holds several
  labels beside one formula keeps them in its text; the review packet still reports every
  page's `boundary_diagnostics` (now only for ink the export would actually lose).

## Projects prepared with 0.6.0-dev.10 or earlier: inline notation and the override channel (0.6.0-dev.11)

- **Nothing changes until a page is re-prepared.** Recorded pages, assets, receipts and
  overrides stay valid; `source verify` reads as before.
- **On re-preparation (`source prepare --replace`, or an override import) these forms cut
  differently and change the page's units and fingerprint:** an inline formula whose closing
  bracket sat on a second native line (`O(n^{-1/2})`), an operator name in the text face next
  to notation inside an inline run (`log c_ε`, `−log P(D)`), a cases block or matrix set in
  running text (now one asset with its rows), and — the churn to expect — a display formula
  that contains such an operator name beside a word space: its crop is unchanged but its glyph
  list gains the space glyphs, so its asset ID moves. Measured on the pilot's 16-page chapter
  with the detector's recorded results, seven pages cut differently (the five defect pages,
  an inline `log n` and a `0 ∈ F, inf_{x∈F} I(x) = 0` that the operator name now joins) and
  seven display pages changed only by the operator-name spaces. A page whose recorded `units`
  override references an asset whose ID moves fails its replay (`unit overrides must reference
  each page asset exactly once`): re-import the review with the new IDs or `--discard-overrides`
  for that page. Re-prepare a reviewed chapter only for the pages that need the fix.
- **Override documents:** a decision that carries `regions` for a page whose ledger already
  records `units` (or the other way round) is now refused; carry the recorded block forward or
  write `"units": null` to drop it. `accepted_grouping_pending` must be a list of
  `{"asset_id", "reason"}` objects; a review file with bare strings fails at import (and a
  legacy receipt carrying one fails verification — re-import that review). Regions written
  with `"glyph_ids": []` for a rule or a figure frame now keep their `kind` and are not
  `grouping_pending`; a page that was accepted with such regions and their forced
  `mixed-region` fallback keeps its receipt until it is re-prepared, after which the rule is
  omitted from reading and the figure is a figure again.

## Projects prepared with 0.6.0-dev.8 or earlier: page-scoped guidance and layout evidence (0.6.0-dev.9)

- **Receipts that failed with `source structure guidance changed since review` because the
  profile file was reformatted or checked out with other line endings verify again** without
  any review: the comparison reads the profile's content, per page.
- **A profile whose base `handling_rules` were extended for a later chapter.** Receipts of
  the earlier chapter are bound to the shorter text, receipts of the later chapter to the
  longer. Run

  ```text
  littrans source rescope PROJECT --packet source-<id> --pages 52-67 --label "Chapter 2" [--dry-run]
  ```

  with a packet the earlier chapter's receipts name (`source verify` lists them in
  `receipt_packets`; `evidence/pages/fidelity-pNNNN.review.json` holds each page's
  `packet_id`). It restores every base rule to the text that packet embeds and moves the
  lines appended after it into one `page_rules` block for the given pages (`scoped_rules`
  lists the keys that grew, `unchanged_rules` the ones that did not); it refuses a rule that
  was rewritten rather than extended after a line break, or that the packet does not know,
  and leaves those to be edited by hand. Both chapters' receipts then verify: a base rule
  split at a line boundary into a scoped block reads the same, and `source verify --pages …`
  names any page and key that still differs. From now on, put a new scope's rules in a
  `page_rules` block; editing a base rule voids every receipt.
- **Older plugin builds refuse a profile with `page_rules`** (`extra` fields are forbidden);
  upgrade every host that shares the project before adding one.
- **A page re-prepared after the upgrade** records `structure.document_profile.guidance_sha256`
  instead of `sha256` in its ledger, so the first `--replace` of a reviewed page under a
  profile changes that page's fingerprint once and needs a fresh receipt; pages that are not
  re-prepared keep theirs. On a page whose page number is detached from the running head, a
  re-preparation also moves an unreferenced figure to its reading position (next to its
  caption) and an omitted running-head rule to the top of the unit order, and a paragraph that
  follows a list item's continuation line becomes a unit of its own; measured on a 16-page
  chapter, every page's unit order changed for the rule alone while units, text and reading
  output changed only on the two pages with those forms. Re-prepare a reviewed chapter only
  for a page that needs the fix, and review it from a fresh packet.
- **Layout results join the record.** In a project's `.gitignore` replace
  `derived/fidelity-layout/` with `derived/fidelity-layout/*.request.json` and
  `derived/fidelity-layout/*.log`, then commit the `derived/fidelity-layout/<fingerprint>.json`
  files that exist (`project tracked` lists them until they are committed). A host that never
  ran the detector can then re-prepare every recorded page, and a page re-prepared alone
  (`--replace --pages 30`) keeps its layout fingerprint and receipt. A result that only ever
  existed on another host stays missing there until that host commits it; until then a
  `--replace` of such a page detects it afresh (with the layout runtime) and reports it in
  `detected_layout_pages`.
- **`source prepare --replace` reuses recorded results by default.** Pass `--redetect` for
  the previous behaviour (a fresh detection of every page) when the detector was upgraded on
  purpose.
- **The scaffolded launcher.** `tools/lt.py` is user-owned and is not rewritten: to get the
  cross-host resolution, delete `tools/lt.py` and run `project scaffold PROJECT` (with
  `--repo-root DIR` for a nested layout), commit it, and give `tools/lt.sh` its executable
  bit once (`git update-index --chmod=+x tools/lt.sh` from Windows, `chmod +x` on POSIX).
  Likewise the generated `.gitattributes` is not rewritten; add `*.cmd text eol=crlf`,
  `*.sh text eol=lf` and `*.py text eol=lf` by hand if the repository is checked out on both
  platforms.
- **`project.yaml` written by `project init` before this build** may hold an absolute
  `source_path`. Edit it to the project-relative `source/<name>.pdf` (and copy the PDF there)
  so a clone on another host finds the PDF.
- **Rendered checkpoints** now link relatively: a `source render` or `finalize` output made
  before the upgrade differs from a new one by its `file:` links only.

## Projects prepared with 0.6.0

No rebuild is needed. Re-preparing pages with `source prepare --replace` (recommended after
this update: negated relations such as `∉` now export precisely and change those units' source
hashes) also stops writing per-region `original.pdf` files, removes them from the directories
it re-exports and reclaims crop directories the new registry no longer references. Run
`source gc --dry-run` to list directories orphaned by earlier `--replace` runs, then
`source gc --apply` to remove them. Fragment records that still name a PDF stay valid until
their page is re-prepared.

