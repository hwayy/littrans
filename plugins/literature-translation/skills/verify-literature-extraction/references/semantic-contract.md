# Source and asset semantics

Each source unit owns its text and ordered asset occurrences. Preserve stable IDs, source PDF fingerprint, ordered page regions, glyph ownership, geometry, baseline and numbering/footnote links. A cross-page complex element may have several ordered original fragments under one logical asset.

The source representation is ordinary text plus `{{asset:ID}}`. Review its fidelity against original pages; never treat OCR or a formula candidate as authoritative source merely because it is grammatical or compilable. Whole original figures and complex tables are valid assets. Where native prose is unavailable, preserve the region and record unresolved text recovery without claiming complete translated coverage.

Review imports bind to the issued source packet and fingerprints. A changed PDF, ownership or meaningful region requires a new review of affected content. Source corrections must use supported project operations; do not manually change a generated registry to clear a gate.

The source gate certifies coverage and boundaries. It does not certify LaTeX, translations or mathematical truth. Independent asset review later certifies the candidate against the original, including equation separation, matrix structure and typography that changes symbol identity.

For a narrow boundary correction, explicit `glyph_ids` select existing original PDF glyph paths. Inspect their visible ink, including accents outside font metrics; the exporter expands bounds to that ink and preserves baseline information. Unsupported or unmapped paths fail closed. Retain the complete raw region and re-review any fallback rather than dropping unknown content. See [glyph correction and footnote rules](../../../references/fidelity-workflow.md#narrow-original-glyph-corrections).

Set `footnote_number` on the footnote and `footnote_refs` on callers using stable footnote unit IDs. Check each link against the original page; missing targets, non-footnote targets and duplicates block source review. Caller and footnote participate in the same source/audit dependency closure.
