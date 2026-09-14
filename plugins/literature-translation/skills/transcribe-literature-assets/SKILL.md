---
name: transcribe-literature-assets
description: Generate structured candidates from original LitTrans image assets and full source context. Use for the transcription lane after source fidelity verification; candidate approval is a separate independent review.
---

# Transcribe Literature Assets

Read [host-runtimes.md](../../references/host-runtimes.md) and [fidelity-workflow.md](../../references/fidelity-workflow.md). Transcription runs in a fresh task using the model and effort recorded in the packet (the project's `agent_models.<host>` configuration); report model unavailability instead of substituting silently.

1. Read the assigned `workflow packet --stage transcribe`, surrounding English, glossary and `original-images.json`. Inspect the original page and every relevant original asset PNG; use larger crops when necessary. Do not read translation candidates or expected answers. For an explicit correction packet, read its bound revision notes and verify the alleged defect against the original before revising.
2. Return LaTeX for formulas, structured content for tables or code, only where supported by the image. Preserve independent equations, explicit line separators, matrix shape, limits, sign, subscripts, conjugation and original notation. Context helps identify a glyph; it does not authorize correcting the author's mathematics.
3. Follow the packet's candidate schema, retaining every asset ID. Record uncertainty or original-image fallback instead of guessing. Whole diagrams stay intact; do not redraw their internal mathematics. Table or code reconstruction must not replace the original asset before independent review.
4. Save the successful candidate response immediately and submit with `assets submit PROJECT INPUT`. Include the packet ID, distinct author task ID, actual model/effort, `image_evidence` mapping each actually inspected required image path to its SHA-256, and available usage; unavailable usage is `null`. Recover malformed serialization offline when possible rather than repeating model work.
5. If independent review rejects a candidate, use a new `assets packet PROJECT --asset-ids IDS --revision-notes "specific review feedback"`; do not overwrite or repeat the original cached response. The new candidate needs a new independent asset audit. Return submitted candidate IDs and unresolved items. Do not approve your own candidate or edit source, translation, glossary or review records.

A candidate is never reliable merely because it compiles or appears plausible. The separate asset reviewer checks the rendered candidate against the original image. Translation can proceed while candidates remain pending.
