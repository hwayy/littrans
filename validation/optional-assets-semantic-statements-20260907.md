# Optional asset enhancement and complete semantic structures

Implemented in the development repository; no commit or generated book artifact is included in this change.

## Behavior

- `workflow next` schedules the translation/audit workflow independently of asset enhancement. `ready_tasks` contains reading work; `optional_asset_tasks` exposes transcription or candidate review with `optional: true`. Status `complete` means reading completion and `assets_complete` reports the separate asset queue. Transcription packets remain available after reading completion. Skills and workflow references no longer mandate parallel transcription.
- Source preparation groups explicitly labeled theorem/lemma/proposition/definition/corollary/claim clauses and their displayed equations by existing `parent_id`. Enumerated children remain separate source units. Proofs, headings and new indented prose end the conservative inferred statement. Batching will not cut a parent group; bilingual rendering places contiguous children in one row while retaining child anchors, clause markup and equation labels.
- Parent groups participate in audit dependency closure and cross-page source evidence. Continuation links skip omitted running material and footnotes and never bridge missing PDF pages. Rendering likewise does not concatenate a continuation across an extraction gap.
- `render --originals-only` forces original images in Markdown and HTML, even when a verified candidate exists, without changing representation state. It omits candidate MathJax and records the selection in render QA.
- A displayed math region may explicitly declare `formula_conditions` as ordered native glyph IDs and their exact source text. Validation rejects wrong ownership, order, duplicates, text mismatch and multi-line declarations. Undeclared recoverable prose remains subject to the existing opaque-prose check. Page approval additionally requires actual independent `formula_conditions_checked` review. Such a formula remains a math asset but has a translatable source unit; QA requires a translated asset companion and rejects a no-language claim. Empty declarations are excluded from serialization, preserving old asset fingerprints.
- An explicit `page_canvas_bbox` source override can expand an unrotated origin-zero page in memory to recover existing content-stream glyphs outside its page box. The immutable PDF and original page image are preserved. Original glyph IDs are matched one-to-one by text/font/size/origin and retained; new glyphs receive `overflow-` IDs, and unmatched old glyphs fail closed. A separately hashed expanded page image enters the ledger and translation evidence. Source approval requires `overflow_canvas_checked`. The coverage overlay uses the expanded image at its actual geometry and links the original canvas.

## Validation

Interpreter: `D:/Repos/Develop/ai/littrans/.venv/Scripts/python.exe`.

- Initial workflow/structure/continuation/footnote regression: 125 tests passed (before the subsequently added condition-negative test).
- Source structure, fidelity source, asset representation, v6 workflow and schema-contract regression after language/canvas additions: 69 tests passed in 9.24 s.
- Fidelity-source regression after stable overflow glyph identity mapping: 22 tests passed in 3.04 s.
- Final original-only renderer integration and overflow-canvas regression after coverage-overlay alignment: 2 tests passed in 1.68 s.
- `git diff --check` passed. The full repository suite was not run. The parent separately owns the safe SVG-group export fix and its tests; those are not counted above.

Tests cover optional post-completion transcription, candidate isolation, verified-candidate original-only rendering, complete theorem batching, child anchors, semantic closure across pages, omitted headers and page gaps, explicit formula-language review, required Chinese companions, wrong language declarations, unchanged source PDF, stable original glyph identity, and recovered overflow ink. Test review receipts refer only to generated synthetic source oracles; no production review or approval was fabricated.

## Limits and book evidence

The QASC 7-page work exposed raw-region inline crops containing neighboring ink, especially on newly added pages. These require reviewed explicit native-glyph overrides and independent image inspection; the statement and scheduling changes do not automatically repair them. The parent fixed conservative handling of remote SVG groups in the glyph exporter and reran its own checks. QASC p220 Eq. 13.14 also contained original right-delimiter/punctuation ink beyond the PDF page box, motivating the explicit overflow contract; Eq. 13.15 contains genuine native condition words inside a complete cases formula, motivating the formula-language contract.

Theorem inference remains conservative and page-local; ambiguous or cross-page statement boundaries still need reviewed overrides. Caption/body separation and algorithm row structure are not universally solved by this change. Noncontiguous page selections preserve page-edge fragments; the book delivery supplies explicit continuation context in its introduction. Final production visual review, translation auditing and original-only rendering remain the parent workflow's responsibility.
