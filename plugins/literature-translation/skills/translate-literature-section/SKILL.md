---
name: translate-literature-section
description: Translate or revise one prepared littrans batch from English into Simplified Chinese with stable unit IDs, protected code and notation, scoped terminology, reader notes, and deterministic QA. Use for technical books, scientific papers, articles, or chapters after prepare-literature-translation has created a batch, including revisions requested by an audit issue list.
---

# Translate Literature Section

Act as the only writer of target text for the batch.

Use the bundled Python launcher described in `../../references/runtime.md` for every `littrans` command.

## Procedure

1. Read `manifest.yaml`, `source.md`, `context.md`, and `output-schema.json` completely, or the equivalent files in a `workflow packet`. On revision, also read the current `translation.jsonl` and all review issues for the batch.
2. Translate only units listed in `translatable_unit_ids`. Preserve every `unit_id` and `source_hash`. Keep each target semantically owned by the source of that same unit: use adjacent context to phrase a seam naturally, but never move, merge, or duplicate source content across stable unit IDs. Do not create translations for display formulas, code, or figures. Preserve verified inline `$...$` LaTeX byte-for-byte inside the translated sentence.
3. Apply [translation-quality.md](references/translation-quality.md). Obey approved glossary entries; treat candidate terms as proposals, not rules.
4. Write one JSON object per line to the batch `translation.jsonl`. For a table unit, translate every cell into `target_table` without changing row or column count; keep `target_text` concise and free of a second ad hoc table. Put uncertainty in `uncertainties`, terminology proposals in `term_proposals`, and current-technology explanations in `reader_note`, never in `target_text`.
5. For a reader note, verify the claim against a current primary official source. Include HTTPS source URLs and access date. Omit the note when verification is unavailable.
6. Run `translation submit`, then `qa run`. A semantic no-op retains its revision and evidence. Fix every deterministic error and rerun both commands. Do not weaken protected-token or numeric checks to make a batch pass.
7. On audit revision, resolve each accepted issue through a new translation revision. Record rejected or waived issues with `review resolve`; do not silently ignore them.
8. Finish only with passing deterministic QA. Report remaining uncertainties and open review issues separately.

## Hard constraints

- Preserve meaning, logical relationships, modality, scope, citations, numbers, units, API names, code, filenames, URLs, exact LaTeX, and table shape.
- Keep literal markup such as `<Button>` as text; do not emit raw HTML or let it alter document structure.
- Restructure sentences for natural Chinese, but do not summarize, embellish, modernize, or explain inside the translation.
- Never edit source units, approved glossary entries, audit files, or another writer's revision concurrently.
