# Native PDF source structure reconstruction

The previous fidelity path exposed native PDF blocks as paragraphs, discarded font emphasis, and left footnote labels and running material in the reading text. This change introduces a source-structure pass before/after original-image preservation.

- Split native blocks at author first-line indents, then combine continuing prose fragments. Display equations remain independently numbered children sharing a logical paragraph `parent_id`; explicit batch selection cannot cut that group.
- Recognize body/definition footnote glyphs using original layout, font size and the footnote definitions. Remove those glyphs from mathematical ownership, emit standard `[^number]` calls, assign `footnote_number` and `footnote_refs`, and combine footnote fragments.
- Coalesce adjacent inline notation into an ordered multi-fragment original asset. All original fragment files and glyph IDs are retained; this does not invent LaTeX.
- Keep running headers/footers and footnote separator evidence as nontranslatable omitted units. A header region must also cover the vertical band of the block before that whole block can be omitted.
- Preserve bold, italic and bold-italic text as Markdown with valid whitespace boundaries. Render strong emphasis and page/column-scoped clickable footnotes; require translated footnote-call multiplicity to match source.
- Record clear page-edge continuation candidates for indented book layouts. Visual source review remains required; these heuristics do not certify arbitrary scanned or multicolumn documents.

Validation: 32 source structure/fidelity/glyph tests, 20 workflow rendering tests, and 9 footnote/continuation tests passed in targeted runs. `git diff --check` passed. The full suite was not rerun for this change. The supplied three-page regression was regenerated using the configured development interpreter and CPU source prepare. Its structure was independently compared with the original pages and changed formula fragments; browser checks independently confirmed loaded original images, working footnote anchors, strong emphasis and omitted running headers. Book-specific artifacts and detailed reviews remain in the book project.

The source hashes and structural plans of the delivered pages were reproduced after the final conservative guard refinements. No source verification receipts or translation approvals were fabricated or reused. This commit also records the existing equation-label duplicate suppression guard, described separately in fidelity-equation-number-rendering-20260907.md.
