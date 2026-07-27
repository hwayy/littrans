---
name: audit-literature-translation
description: Independently audit a completed littrans translation batch for fidelity, omissions, additions, terminology, technical accuracy, citations, numbers, and Chinese style without editing target text. Use after deterministic QA passes, before machine approval, or after a revision requires targeted re-review.
---

# Audit Literature Translation

Audit read-only and emit issues, never a replacement translation.

Use the bundled Python launcher described in `../../references/runtime.md` for every `littrans` command.

## Independence

- Prefer a fresh task or independent subagent that receives only the raw batch artifacts. Do not provide the expected verdict, prior reviewer conclusions, or translator rationale beyond recorded uncertainties.
- When subagents are available, run three read-only lenses independently: fidelity, technical/terminology, and Chinese editing. Merge duplicate findings after all lenses finish.
- When independent execution is unavailable, perform the three passes sequentially and disclose that limitation.

## Procedure

1. Read the batch manifest, source, context, submitted translation, approved glossary, and protected tokens.
2. Compare every translatable unit under all three lenses. Use [issue-contract.md](references/issue-contract.md).
3. Check non-translatable neighbors and structural metadata against the PDF: verify display and inline LaTeX, equation numbers, code indentation/language, figure-label translations, captions, paragraph continuation, titled sidebar grouping, and cross-references. Check every source/target table cell and its row/column alignment. Do not rewrite protected code or formulas.
4. Write JSONL issues only. Use precise source and target spans, explain the actual defect, and propose a revision only when confident.
5. Import the issue file with `review import` even when it is empty; this records completion of all three lenses.
6. Report issue counts and all blocker/major findings. Do not run `translation submit`, resolve findings, or approve the batch.

If the project enables `external_review`, the internal audit remains mandatory and precedes
the external stage. Follow [external-review.md](references/external-review.md) only after
machine approval; external reviewers do not replace the three internal lenses.
