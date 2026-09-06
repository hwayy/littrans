---
name: verify-literature-extraction
description: Verify source completeness, reading order and original-image boundaries in LitTrans schema 6. Use before translation or after source changes; formula transcription accuracy is reviewed separately.
---

# Verify Literature Extraction

The pretranslation gate is faithful, complete source preservation. Use [runtime.md](../../references/runtime.md) and [semantic-contract.md](references/semantic-contract.md).

1. Generate `source review-packets` for the selected pages. Read the packet manifest, source units, asset regions, unassigned glyphs and overlay report.
2. Inspect every original page alongside its overlay. Work from the original page outward, including small variables, footnotes, captions, tables, raster labels, running material and blank pages; do not inspect only detected candidates.
3. Check reading order, paragraph and cross-page continuity, equation numbers, `footnote_number`/`footnote_refs` ownership and links, image boundaries and duplicate prose. Whole figures may own their internal mathematics. Preserve uncertain complex regions intact, with an explicit grouping decision, rather than silently discarding them.
4. Return the packet-bound review decisions using its emitted schema. Record source/layout defects precisely. Import through `source import-review` with the required visual-review attestation only after actually inspecting the images.
5. Rerun `source verify`. Fix uncovered source content or stale evidence through supported source corrections and new review packets. Source PDF/region changes invalidate the affected verification; never reuse a receipt with a changed fingerprint.

A faithful original image may pass while its LaTeX remains unfinished. Missing mathematical understanding belongs in translation uncertainties; it is different from missing source content. Do not infer zero omissions from aggregate detector scores or an enclosing page box.
