---
name: prepare-literature-translation
description: Prepare a PDF technical book, research paper, article, or chapter for controlled translation. Use when Codex must initialize or resume a littrans project, inspect a text-bearing PDF, extract stable source units and assets, correct layout classifications, write the document brief and style guide, or propose terminology before body translation.
---

# Prepare Literature Translation

Prepare the source and stop before translating body text.

## Procedure

1. Resolve this skill directory, then resolve the plugin root as `../..`. Run `python <plugin-root>/scripts/littrans.py doctor` as described in `../../references/runtime.md`. On first use, disclose that the launcher bootstraps an isolated per-user environment and may need package-index access.
2. If `project.yaml` is absent, run `project init` with the source PDF, a private project directory, and either `technical-book` or `research-paper`.
3. Run `source inspect`. Treat every page with no usable text layer as a blocker; do not attempt OCR in this version.
4. Run `source extract` for the requested PDF pages. Read `derived/extraction-issues.jsonl` and `derived/document.json` completely.
5. Invoke `$verify-literature-extraction` for all selected pages. It must compare the visual overlay with the PDF and verify paragraph boundaries, exact inline/display LaTeX, structured tables, code indentation/language, notes, images, labels, captions, footnotes, and references. Follow [extraction-review.md](references/extraction-review.md).
6. Correct durable decisions through `overrides/layout.yaml`, run `source apply-overrides`, and rerun `source verify`. Before translation, re-extraction with `--replace` is allowed; after translation begins, preserve IDs, refresh affected batches, and expect semantic changes to invalidate prior QA/review status. Never edit `derived/units.jsonl` directly.
7. Replace the placeholder in `context/document-brief.md` with the subject, argument, audience, document structure, source style, symbol conventions, and genuine uncertainties. Update `context/style-guide.md` only with project-specific rules.
8. Add uncertain terms to `glossary/candidates.yaml`. Promote a term to `approved.yaml` only when the user or authoritative project evidence supports it.
9. Run `batch create` only after `source verify` passes. Report the visual report, created batches, corrections, and candidate terminology.

## Stop conditions

- Stop on scanned pages, ambiguous formulas, unresolved reading order or paragraph continuity, image-only tables, untranslated figure labels, missing captions, or a source-hash change.
- Do not translate body units, invent definitions, approve terminology by fluency alone, or mix reader notes into the source.
