# Extraction review contract

Inspect the PDF visually as well as through extracted text.

## Required checks

- Match every selected PDF page to at least one structural unit or an explicit extraction issue.
- Exclude repeated headers, footers, watermarks, plot ticks, and decorative labels from translatable prose.
- Preserve source order across columns. Keep headings with their following content.
- Store code as non-translatable text with exact indentation and a language tag.
- Store formula crops only as review evidence. Store all inline and display mathematics as visually verified LaTeX.
- Translate captions separately. Represent every table as verified rectangular rows; an image-only table blocks translation.
- Inspect every figure or screenshot and record Chinese translations for meaningful internal labels.
- Join logical paragraphs split by PDF blocks or page boundaries.
- Keep page number, bounding box, unit ID, and source hash traceable.

## Layout override file

Create `overrides/layout.yaml` with a top-level `overrides` list. Match by `page` and either `unit_id` or an intersecting `bbox`.
For a reviewed bulk correction, match `current_kind` and an optional `text_regex`; list narrower rules before broader ones.

```yaml
overrides:
  - page: 10
    bbox: [80, 100, 500, 420]
    kind: figure
    translatable: false
  - page: 61
    unit_id: p0061-u004-abcd1234
    kind: code
    translatable: false
  - page: 3
    unit_id: p0003-u010-abcd1234
    ignore: true
```

Use `source_text` only to correct demonstrable OCR text against the visible PDF. Record a `reason` for text corrections.

Set semantic fields such as `latex`, `source_markdown`, `table`, `code_language`, continuation flags, and `figure_labels` in the same override. `verified: true` requires an evidence-based `reason` and is never inferred from extraction confidence.

For running heads, decorative separators, and other source matter that should stay traceable but must not enter the reading edition, set `render_policy: omit`. This also makes the unit non-translatable. Do not use `ignore: true` for this purpose because ignoring removes the unit from the source inventory.

Run `littrans source apply-overrides <project>`. The command preserves unit IDs, rejects destructive source-text changes after translation, and invalidates prior QA/review status when semantic source data changes. Rerun `source verify`, refresh affected batches, and re-audit revised translations.
