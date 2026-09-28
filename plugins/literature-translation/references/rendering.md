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

A paragraph continues across a page edge when the sender's `continued_to_next` is set (its
last line ends mid-sentence), or when the receiver's `continues_from_previous` is set and the
sender's text does not end in terminal punctuation; the receiver's flag is never set on a unit
that opens with a bold run-in label, a theorem statement, `Proof` or a list label, nor on one
whose first letter is a capital (`This gives`, `Then` open a sentence; `where the notation …`
continues one; a script without letter case keeps the geometric reading). The flags express
a continued sentence; a container that continues on the next page (a proof, an exercise) is
recorded by a reviewed `parent_id` override, never inferred. A figure, table or caption set
at the page top or bottom is a float: the flags are decided on the first body unit after the
floats at the top and the last one before the floats at the bottom. Batching and audit
closure read the same pair of flags, across any floats between the sender and the receiver.

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
