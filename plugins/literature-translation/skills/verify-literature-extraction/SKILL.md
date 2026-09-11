---
name: verify-literature-extraction
description: Verify source completeness, reading order and original-image boundaries in LitTrans schema 6. Use before translation or after source changes; formula transcription accuracy is reviewed separately.
---

# Verify Literature Extraction

The pretranslation gate is faithful, complete source preservation. Use [runtime.md](../../references/runtime.md) and [semantic-contract.md](references/semantic-contract.md).

1. Generate `source review-packets` for the selected pages. Read the packet manifest, source units, asset regions, unassigned glyphs and overlay report, including its bound document-structure profile. Treat profile rules as hypotheses to check against the original; report newly encountered forms and update the profile before creating a fresh packet.
2. Inspect every original page alongside its overlay. Work from the original page outward, including small variables, footnotes, captions, tables, raster labels, running material and blank pages; do not inspect only detected candidates.
3. Check complete document-specific containers and their closing marks, list lead-ins and real text bullets, paragraphs continuing after displays (including “where” clauses), reading order, paragraph and cross-page continuity, equation numbers, `footnote_number`/`footnote_refs` ownership and links, image boundaries and duplicate prose. Whole figures may own their internal mathematics. Preserve uncertain complex regions intact, with an explicit grouping decision, rather than silently discarding them.
4. Return the packet-bound review decisions using its emitted schema. Record source/layout defects precisely. Import through `source import-review` with the required visual-review attestation only after actually inspecting the images.
5. Rerun `source verify`. Fix uncovered source content or stale evidence through supported source corrections and new review packets. Source PDF/region changes invalidate the affected verification; never reuse a receipt with a changed fingerprint.
6. Run `source render PROJECT --pages PAGES` and read the checkpoint HTML (`output/source-pNNNN-pNNNN.html`) as a document: headings, figures with captions, list items, displayed lines and inline notation must read in order with tight original images. Any defect still visible there goes back through `source import-review`; only then create batches or hand the pages to a translator. Add `--standalone` when the checkpoint is to be shared as one file (assets embedded).

A faithful original image may pass while its LaTeX remains unfinished. Missing mathematical understanding belongs in translation uncertainties; it is different from missing source content. Do not infer zero omissions from aggregate detector scores or an enclosing page box.

For a displayed formula with source-native condition words, review its explicit `formula_conditions` entries (ordered original `glyph_ids` plus exact `source_text`). Confirm that each is language inside the formula, not neighboring prose swallowed by the region, before setting `formula_conditions_checked`. These assets require translated companions even though their mathematical image remains intact.

When original content-stream ink lies beyond the PDF page box, a source override may specify `page_canvas_bbox` to expand an unrotated origin-zero page in memory. Inspect the original page, the separately hashed overflow canvas image, and the recovered fragment before setting `overflow_canvas_checked`. This does not rewrite the source PDF or reconstruct missing characters. Reject arbitrary canvas expansion without existing off-page native glyphs.
