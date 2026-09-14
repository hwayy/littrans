# Native prose and mathematical region separation

Date: 2026-09-07. This record concerns the generic LitTrans development code; source documents, regenerated units, asset images and book-specific review records remain in the translation project.

## Defect and change

The previous `_regions` overlap loop expanded a formula rectangle to entire native PDF blocks whenever it touched ordinary prose. Transitive rectangle merging could then absorb a paragraph and adjacent display equations. Preserving the enclosing image did not make the resulting text suitable for translation.

The new path measures original SVG glyph ink for grouping, assigns mathematical glyph IDs explicitly, excludes whitespace metric boxes from detector ownership, and stops promoting math/prose intersections to whole PDF blocks. Native math runs remain available when a detector box overlaps prose. Adjacent native lines require compatible baseline geometry; detector regions merge by shared ownership rather than arbitrary rectangle overlap. Original fraction bars and glyph paths are retained. Normal-font operators and separate math roman/bold fonts are recognized without treating plain CMR prose as math.

Tables and figures keep complete raw-region ownership when meeting a math component. Unsupported precise export keeps raw original evidence with explicit mixed-region/grouping-pending provenance. Unsupported disjoint vector paths are measured before being excluded from a precise fragment.

Display equations become independent units, with printed equation numbers attached as metadata. This prevents a display equation inside a native paragraph block from being selectable only as opaque prose. Inline LaTeX still enters through independently reviewed candidates; the translation author preserves inline asset markers and must not substitute vague verbal categories for mathematical referents.

A new source verification check rejects math/mixed assets containing six or more recoverable ordinary words (excluding mathematical operators), including old receipts that previously approved opaque paragraph images. This is a conservative additional gate, not a proof of complete parsing for shorter text or scanned pages.

## Validation

- 20 fidelity-source tests pass, including new regressions for adjacent math lines, intact table ownership, normal-font display operators, independently numbered display units, opaque-prose rejection, and unsupported-vector original fallback.
- Independent code review reproduced four geometry/ownership/export defects and one operator-gate false positive. Each has been corrected; corresponding cases are included in the tests.
- A supplied three-page technical-book regression was rerun through the actual development interpreter and CPU `source prepare`, first in an isolated book-local validation project and then in the book project. No extraction registries or approval evidence were edited to force acceptance.
- Full-suite result is recorded after completion below.

## Remaining limits

This change does not certify native PDF block order as logical paragraph order. Footnote relationships, line-wrapped formulas, headers and cross-page boundaries still require independent source review. Raw fallback and mathematically uncertain content do not become verified structured expressions. Book-specific translations require regeneration and their normal review sequence after source correction.

### Completed test runs

`python -m pytest plugins/literature-translation/tests -q --disable-warnings --tb=short` completed. The only failure was the previous glyph-export assertion that even a disjoint circle anywhere on a page must reject a fragment; two environment-dependent tests were skipped. That assertion was updated to test both an intersecting unsupported circle (must reject) and a disjoint circle (must preserve the selected glyph and omit only the disjoint path).

Final targeted rerun: `python -m pytest plugins/literature-translation/tests/test_glyph_export.py plugins/literature-translation/tests/test_fidelity_source.py -q --disable-warnings --tb=short` — all 25 tests passed. The full suite was not rerun a second time after that test correction and the final article/normal-operator regression additions. `git diff --check` passed.

Independent source-image review additionally found a prose article preceding a math-font space entering the formula prefix. Prefix recognition now ignores math-font whitespace; the new regression passes and the source reviewer confirmed the affected crop and source text after regeneration. Book-specific footnote and logical boundary findings remain explicitly unapproved in the book project.
