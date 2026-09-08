---
name: prepare-literature-translation
description: Prepare a PDF as faithful text and original-image assets for LitTrans schema 6. Use to initialize or rebuild a project, preserve source content, establish reading order, and prepare context before translation.
---

# Prepare Literature Translation

Use the launcher in [runtime.md](../../references/runtime.md). The single preparation workflow preserves source content before any formula transcription.

1. Run `doctor`. Initialize a new project with `project init`, or use `project rebuild OLD NEW` for a pre-v6 project. Rebuild copies the PDF, document context and glossary into a new directory; it does not inherit extracted units, translations or approval evidence.
2. Run `source prepare` for the requested PDF pages. It combines native glyph geometry with the isolated layout detector. Formulas, complex tables, code and figures become original-image assets; it does not decode formulas. Do not offer extraction-mode choices.
3. Read the coverage report and source units. Preserve ordinary prose with stable `{{asset:ID}}` references. Keep circuit diagrams whole and captions separate. If a clean boundary is uncertain, preserve the complete line or region and record the grouping problem.
4. Use `verify-literature-extraction` to inspect original pages and coverage overlays, resolve missing or duplicated content and boundary errors, then run `source verify`. No-text pages and missing layout-model results require explicit coverage review; neither a page-sized image nor an empty warning list establishes complete source ownership.
5. Write the document brief and project-specific style guide. Record proposed terminology separately from approved terms. Create batches after fidelity verification passes, preserving paragraph, theorem and derivation boundaries.
6. Hand verified source context to a fresh translation task. Formula transcription is optional enhancement and may be scheduled independently at any later time, including after delivery. If selected, give its independent task the same original source and images. Formula LaTeX and reconstructed table/code structure are not prerequisites for translation.

Read [extraction-review.md](references/extraction-review.md) for coverage checks. Do not edit generated units or asset registries directly. Report missing evidence without claiming source verification.
