---
name: verify-literature-extraction
description: Audit and correct a littrans PDF extraction before translation. Use when verifying page completeness, reading order, paragraph boundaries, inline or display LaTeX, structured tables, code indentation and language, notes, figures, screenshots, captions, footnotes, or references against the original PDF and clearing the source-verification gate.
---

# Verify Literature Extraction

Treat the PDF as authoritative. Do not translate body prose in this skill.

## Procedure

1. Run `source verify` through the launcher in `../../references/runtime.md`. Open `derived/extraction-report.html` and read `derived/verification.json` completely.
2. Compare every selected PDF page with its overlay and unit inventory. Check page coverage, reading order, paragraph continuity, headings, lists, notes, code, formulas, tables, figures, captions, footnotes, and references. Read [semantic-contract.md](references/semantic-contract.md).
3. Transcribe every display formula to exact LaTeX in the unit `latex` field. Transcribe every inline expression inside `source_markdown` with `$...$`. Preserve equation numbers separately. Use the crop only as review evidence; never approve image-only math.
4. Convert each table to rectangular `table.rows`. Preserve merged-header meaning through `header_rows`; verify every cell and numeric value. Never approve a table crop as the final representation.
5. Restore code indentation exactly, set `code_language`, and confirm that literal tags such as `<Button>` are inside code or remain literal prose. Mark Note/Tip/Warning/Caution/“What’s New” blocks as `note` and set the explicit `callout_kind` (`note`, `tip`, `warning`, `caution`, or `whats-new`). For a titled multi-paragraph sidebar, preserve the original heading/paragraph kinds and assign one shared `sidebar_id`, with `sidebar_role: title` on its heading and `sidebar_role: body` on every contained unit.
6. Join mistaken prose continuations with `continues_from_previous` and `continued_to_next`. Correct dropped, duplicated, hyphenated, or control characters in `source_text` with an explicit reason.
7. Inspect each figure or screenshot. Add all meaningful internal labels to `figure_labels` with Chinese targets. If no meaningful text exists, record that fact in the override reason.
8. Write durable changes to `overrides/layout.yaml`, including `verified: true` and a concrete evidence-based reason for each verified semantic unit. Run `source apply-overrides`, then rerun `source verify`. Matching page receipts are reused automatically; use `--force` for a deliberate full recheck.
9. Finish only when verification passes. Return the visual report path, corrections made, and any true blocker.

## Integrity rules

- Never mark a formula, table, figure, or code block verified without comparing it to the rendered PDF page. For a table, inspect beyond the crop and onto the following page so a header plus first row cannot masquerade as a complete table.
- Never infer missing formula structure from broken extracted text when the crop is ambiguous; stop and request a second visual review.
- Never edit `derived/units.jsonl` directly or suppress a verification code merely to create batches.
