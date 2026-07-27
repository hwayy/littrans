# Translation quality contract

## Fidelity

- Preserve claims, qualifications, negation, causality, comparison, uncertainty, and author stance.
- Translate all meaningful prose exactly once. Do not absorb a caption, note, or footnote into another unit.
- Keep technical notation and cited identifiers unchanged unless the approved glossary explicitly governs prose around them.

## Chinese writing

- Prefer clear contemporary Simplified Chinese and natural clause order.
- Break an overloaded English sentence only when references and logical relationships remain explicit.
- Avoid translationese, vague pronouns, needless nominalization, and unsupported connective words.

## Technical material

- Preserve verified inline LaTeX exactly. Display formulas are rendered from verified LaTeX and are not translated.
- Translate each table cell into `target_table` while preserving dimensions, numbers, units, references, and row meaning.
- Preserve code bodies and indentation. Let the renderer fence and highlight code according to `code_language`.
- Write semantic body text only in `target_text`: omit Markdown heading markers, list bullets or numbers, admonition blockquotes and labels, caption emphasis, and footnote labels. The renderer owns those wrappers.
- Treat literal markup names such as `<Button>` as text, never as executable Markdown or HTML structure.
- Translate UI strings or code comments only when the project profile marks them translatable.
- Retain citation anchors and reference numbers. Translate bibliographic titles only when the project policy explicitly requires it.

## Uncertainty

- Keep an uncertain but faithful rendering in `target_text` when possible.
- Record the exact ambiguity and alternatives in `uncertainties`.
- Propose a recurring term in `term_proposals`; do not self-approve it.
