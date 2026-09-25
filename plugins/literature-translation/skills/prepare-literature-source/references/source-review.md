# Source review

The procedure a `literature-source-reviewer` subagent follows for its assigned page range. The pretranslation gate is faithful, complete source preservation. It certifies coverage, boundaries and reading structure; it does not certify LaTeX, translations or mathematical truth. Contracts in detail: [fidelity-workflow.md](../../../references/fidelity-workflow.md#source-review-packets-decisions-and-overrides). Launcher: [runtime.md](../../../references/runtime.md).

## Scope and authority

- You may run these commands, for your assigned pages only: `source review-packets PROJECT --pages P --host HOST`, `source import-review PROJECT REVIEW.json --confirm-visual-review`, `source render PROJECT --pages RANGE` and `source verify PROJECT --pages RANGE`. Run `source prepare PROJECT --pages N --replace` only when an import error for page N names it as the remedy (its recorded layout result is missing).
- Decide only your assigned pages. A neighbour page that a packet adds as evidence is context; leave its decision out.
- Never edit `context/source-structure.json`, generated units, asset registries, ledgers or receipts. Never delete a packet that a receipt names (`receipt_packets`). Never pass `--redetect` or `--discard-overrides`. Never create batches, translate or transcribe.
- Never install packages or change any Python environment, and write nothing outside the project. Take measurements from the packet: `packet.json` → `pages[].ledger.glyphs` gives every glyph's `bbox`, `origin`, `font`, `size` and `line`, and `pages[].ledger.structure` gives the page's `margin`, `first_x`, `indent_style`, body `font_size` and, on a page with lists, `list_items`. Compare indents, faces, sizes and white space from those values and the page images. Report anything they cannot settle instead of building a tool for it.
- Set `reviewer` to your agent name and an identifier of this task: the host's task id if you know it, otherwise your page range (for example `literature-source-reviewer/p0012-p0021`). Save each decision file as `packets/<packet_id>/review.json`; the receipt keeps the decision.
- `source import-review` resolves `REVIEW.json` against the current directory, not against PROJECT: pass `PROJECT/packets/<packet_id>/review.json` or an absolute path.

## Loop

1. Read the packet: `packet.json` (units, assets with their regions, the page ledger with unassigned glyphs, the bound `document_structure`), `coverage.html` with its page overlays, and `review-template.json`. Each template page carries a `context` block: declared `formula_conditions`, `grouping_pending` asset IDs, `boundary_diagnostics`, blocking `findings` and the `structure_checks` to confirm one by one. Confirm those lists against the original; do not guess from crops.
2. Review every assigned page against its original image as described below, and fill its decision in the template.
3. Import with `--confirm-visual-review` only after you have actually viewed each page image and crop you attest. Read the result:
   - `approved_pages` are done.
   - `rejected_pages` lists the failures of each page (they are also in the receipt's `failures`): fix the decision or correct the page.
   - `changed_pages` (corrected), `deferred_pages` (a dependency moved within this review) and `invalidated_pages` (a dependency outside it moved) all need a fresh packet and a new review.
   A review file may hold only some of the packet's pages. Pages you leave out keep their state; this is how you hold a page back (for a proposed rule, or for a correction you are still working out) while you import the others.
4. Create a fresh packet for the pages that still need review and repeat. After three rounds without approval, stop and report the page as blocked, with the reason. A second round for one page works the same way: for page 256, run `source review-packets PROJECT --pages 256 --host HOST`, decide page 256 alone in that packet's template (the packet may add neighbouring pages as evidence; leave them out), save it as `PROJECT/packets/<new packet_id>/review.json` and import it.
5. Run `source render PROJECT --pages RANGE` and read the checkpoint (`output/source-pNNNN-pNNNN.html`) as a reader would: headings, figures with captions, list items, displayed lines and inline notation must read in order, with tight original images. If you cannot view the rendered page, read its HTML text and the images it links to. Send any reading defect you still see back through the loop. The checkpoint's attention list also repeats page conditions (a missing layout detector, for instance); report those rather than looping on them.

## What to check

Work from the original page outward, not from the detected candidates. Account for all prose and mathematics: small variables, accents, brackets, footnotes, captions, tables, raster figure labels, running material and blank pages. Track unassigned and multiply assigned glyphs. Neither a detector score, a page-sized image nor an empty warning list proves completeness.

- **Crops.** Check complete ascenders, descenders, superscripts, subscripts, conjugation bars, radicals and delimiter extents. An inline crop must hold no surrounding quotation marks, no hyphens of neighbouring words and no sentence punctuation. A display must not swallow a trailing phrase such as "for all t > 0". A bold single letter in prose is notation and must be an asset. The detector rectangle is only a proposal: do not grow a formula to a whole native text block because font metric boxes overlap. When segmentation is ambiguous, prefer an intact line or region and record the grouping decision.
- **Delimiters.** Judge ownership in the full surrounding prose, including the previous and next lines and continuation pages. A prose parenthesis such as `(as a function of size n)` keeps both parentheses outside the inline `n`. A citation such as `[JSW+25]` stays native text even when its `+` is set in a math font. Resolve every `prose-boundary-in-math` diagnostic against the PDF before you set `boundaries_complete`. Never fix this by stripping unmatched delimiters: intervals, function arguments and expressions that continue across lines or pages can legitimately be unbalanced.
- **Prose stays text.** Recoverable native prose must remain text with separate inline `{{asset:ID}}` references. A `mixed-region` asset holding six or more ordinary words is not coverage (`recoverable-prose-in-image`): split the region. Whole figures and circuit diagrams stay intact and own their interior labels; their captions are separate units. Complex tables and code may stay whole original images.
- **Structure, unit by unit.** A unit's `kind` is only as good as the detector box it came from. For every heading, caption and run-in label (theorem, example, proof, step), compare its face, size and indent, and the white space around it, with the page's running text and with the same form on neighbouring pages. An italic step line, a run-in theorem line or a sentence that mentions a figure ("Figure 10.2 shows …") is a paragraph. At every native block joined into the unit before it, look for a paragraph break the join ran over (an indent, extra white space, a new item). Follow each list from its lead-in through the items to the displays and explanation paragraphs inside each item. A list container is flat: the lead-in is the parent of all of them, and an item-level parent is an override choice.
- **Order and links.** Check reading order; paragraphs that continue after a display (including "where" clauses); paragraph and cross-page continuity; containers and their closing marks; list lead-ins and real text bullets; and duplicated prose. Every display must be independently addressable with its printed equation number. Put `footnote_number` on the footnote and `footnote_refs` on each caller; check every link against the original.
- **Profile rules** are hypotheses to test against the original. A form is new only when no base rule, no `page_rules` block covering the page and no check in this document decides it; a form one of them already decides (a run-in bold label that stays inside its paragraph, say) is judged by that rule and is not new. For a new form, propose a `page_rules` block for the pages that print it, leave those pages out of your review file and report them as not approved. Never approve a page and propose a rule for it in the same report. A packet without `document_structure` has no profile: judge each form from the page itself and mention the missing profile in your report.

## Decisions

Fill every field the template asks for (see [decision fields](../../../references/fidelity-workflow.md#decision-fields)):

- **Flags.** `viewed_original`, `coverage_complete`, `boundaries_complete`, `reading_order_correct` and `grouping_checked` are always required. `layout_fallback_checked` is required when layout is not `ok`, `formula_conditions_checked` when conditions are declared, and `overflow_canvas_checked` when there is a canvas override. Leave a flag that does not apply `false`.
- **Structure checks.** Put every row of `structure_checks.roles` into `confirmed_roles` as `{"unit_id", "kind"}`, with the kind you read on the original. Put every row of `structure_checks.joins` into `confirmed_joins` as `{"block"}` once you have checked there is no paragraph break at it. If you read a kind differently (`role-disputed`), or find a break at a join, correct the page with an override instead of confirming the row. The lists hold what preparation knows it decided; text it misplaced without knowing (prose left beside a split formula, say) appears only on the original.
- **Pending grouping.** An asset with pending grouping blocks approval until you either correct it or list it in `accepted_grouping_pending` as `{"asset_id", "reason"}`.
- **Formula conditions.** For a math asset with source-native words (a cases formula's condition words, an inline `i.o.` or `a.s.`), check each declared condition (its ordered `glyph_ids` and exact `source_text`). The words are inside the crop by construction. The question is whether each declaration is right and whether any language in the crop is missing (`undeclared-formula-language`). A `math` region or preserved `math` asset without `formula_conditions` is declared automatically; write the list yourself only to overrule it.
- **Issues.** `issues` must be empty for approval. Record source and layout defects precisely in `notes` and in your report.

## Corrections

Correct a page by adding an `override` to its decision, using `regions`, `units` or `page_canvas_bbox` (see the [override contract](../../../references/fidelity-workflow.md#override-contract)). A decision that carries an override is not an approval: the import re-prepares the page from it and ignores its flags, so the corrected page is approved only from its fresh packet. Prefer `regions` alone and let structure assembly rebuild the units; write `units` only when assembly cannot express the reading.

- **Carry blocks forward.** An override replaces the one the page's ledger already records, block by block. Carry forward any recorded `units` or `regions` block you do not change, or drop it on purpose with `null`; an import refuses a decision that would silently retire one.
- **Unit overrides.** Across the page's `units`, every page asset must be referenced exactly once. Keep an unchanged asset through a `regions` entry with `preserve_asset_id`, and give a new region an explicit `id` that its unit can reference (the default id is a content hash known only after import). Copy region and fragment boxes from the packet so the crops reproduce byte for byte.
- **Glyph-free regions.** A region that owns no glyphs (a rule, a figure frame) is written without `glyph_ids` or with an empty list. Both produce a raw crop that keeps the declared `kind`.
- **Narrow corrections.** Explicit `glyph_ids` select existing original glyph paths, and the exporter grows the bounds to their visible ink. Unsupported or unmapped paths fail closed: keep the complete raw region instead of dropping content (see [narrow corrections](../../../references/fidelity-workflow.md#narrow-original-glyph-corrections)).
- **Page canvas.** `page_canvas_bbox` expands an unrotated, origin-zero page in memory, and only when native glyphs already lie beyond the page box. Inspect the original page, the separately hashed overflow image and the recovered fragment before you set `overflow_canvas_checked`. Never expand the canvas arbitrarily.
- **Missing layout result.** An import refuses a corrected page whose recorded layout result is missing from `derived/fidelity-layout/`. Re-prepare that page with `--replace` and review a new packet.
- **Guidance changed.** `fidelity-source-unverified` with `source structure guidance changed since review for page N` means the coordinator changed that page's rules. Review the page from a fresh packet.

## Report

Return one JSON object to the coordinator:

```json
{
  "pages": "RANGE",
  "approved_pages": [12, 13],
  "blocked_pages": [{"page": 14, "reason": "…"}],
  "proposed_page_rules": [{"pages": "14-15", "handling_rules": {"…": "…"}, "evidence": "what the original shows"}],
  "outside_invalidated_pages": [16],
  "defects": ["source or layout defects recorded, with page and unit or asset IDs"],
  "checkpoint": "output/source-p0012-p0015.html"
}
```

A faithful original image may pass while its LaTeX is unfinished. Missing mathematical understanding belongs to translation, not to this gate. Missing source content does belong here.
