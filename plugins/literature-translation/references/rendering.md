# Rendering

## Rendering

A project without any transcription candidate renders originals-only automatically; render QA
records `originals_only_reason: no-transcription-candidates`. For an explicitly all-original
reading edition of a project with candidates, render with `--originals-only`
(`originals_only_reason: requested`). Either way original-image representation is forced in both
Markdown and bilingual HTML without changing any candidate or review, and the candidate MathJax
bootstrap is omitted. The edition header, `*.quality.md` and `render-qa.json` (`rendered_status`)
reflect the lowest record status among the rendered units, not the project-wide status. Formal
dependency cover selection considers only current QA/audit/external evidence.

Paragraph fragments with the same reviewed `paragraph_id` form a continuation chain,
including across adjacent pages and intervening floats. A new paragraph keeps a separate ID;
item and container membership do not merge its text with the preceding paragraph.

Labels are assembled from their recorded parts, with literal text unchanged and language
parts translated. Only the logical paragraph head displays the label. Numbered body paragraphs
remain paragraphs, and bullets appear only when present in the source label. Layout uses the
recorded indentation and alignment, allowing a translated label to expand without hiding text.

Outline levels 1–6 render as HTML `h1`–`h6` and Markdown headings. Deeper levels use HTML
`role="heading"` with `aria-level`; Markdown also uses embedded HTML when layout cannot be
represented faithfully. The contents combines each heading's label and all body fragments
once, preserving its outline level. Target contents uses translated language parts and titles.

Reading output appends image-language companions after a complete continuation chain; footnote
companions remain inside their Markdown definitions. Markdown footnote calls and definitions use
unique labels derived from the referenced source unit ID, so repeated numbers on different pages
do not collide; definitions are indented after asset/companion expansion, and continued table
fragments with independent footnote scopes remain separate. Continued-table rendering includes
companions from every fragment, using each fragment's original source context for labels and
footnote scope. Code/math literals remain literal, including dollar and backslash inline/display
delimiters and multiline backtick/tilde fences.

Rendered pages under `output/` link the original page images, the source PDF (`#page=N`) and
the copied original assets by relative, percent-encoded paths, so the same tree renders the
same bytes on every host and a rendered checkpoint can be compared across machines; only a
source PDF kept outside the project root is still linked by a `file:` URI.

Edition publication snapshots all shared MathJax files, including absent incoming paths, so a
later failure restores prior bytes and removes newly created runtime files; individual runtime
copies are atomic. Pydantic >=2.12 is required for conditional identity-field serialization.
