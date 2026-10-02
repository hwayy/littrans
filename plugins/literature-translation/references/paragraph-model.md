# Paragraph model

SourceUnit v4 represents headings, numbered or bulleted text and ordinary prose as
`kind: paragraph`. TranslationRecord v3 stores body and label translations separately.
Equations, figures, tables, captions, footnotes and code retain their own kinds.

## Paragraph properties

| Field | Meaning |
| --- | --- |
| `paragraph.type` | `plain`, `numbered`, `bulleted` or `labelled`; describes label form. |
| `paragraph.outline_level` | `null` for body text; a positive integer for an outline heading, with no six-level limit. |
| `paragraph.body_text/body_markdown` | Explicit body or title text, excluding the label. |
| `paragraph.label` | Ordered label parts and their source evidence; absent on plain paragraphs and continuation fragments. |
| `paragraph.layout` | Indentation, alignment, spacing and reference font size in PDF points. |
| `paragraph.candidates` | Candidate evidence from preparation. |

`source_text/source_markdown` retain the complete source. Outline level is independent of
type, numbering and typography: a numbered body paragraph has no outline level, and a sidebar
title has none unless explicitly confirmed. Label and body must cover the source without
omission or duplication. A heading may consist only of a label, with an empty body.

## Labels and evidence

Each part has a stable `part_id`, nonempty `source_text`, optional `source_markdown` and
`policy: literal|translatable`. Parts retain their source order. Numbered and bulleted labels
use literal parts; a language label such as `Choice 1.` can split into translatable `Choice`
and literal ` 1.`. Arbitrary Unicode labels are valid; numbering patterns do not determine
outline levels or membership.

Without `source_markdown`, a part is literal text, including stars, underscores and HTML
characters. Explicit inline formatting is retained in its translation.
`label_translations` maps every translatable part ID to nonempty translated text; literal
and unknown IDs are invalid. `target_text` contains only the body or title text.

`label.evidence` records the one-based `page`, ordered `glyph_ids` and optional `bbox`.
When native glyph data is unavailable, `source_range: [start, end]` records Unicode character
offsets in `source_text`, with an exclusive end. The parts must reproduce the recorded source;
glyph evidence must belong to the paragraph and preserve native order and geometry.

## Layout and relationships

Layout is relative to the containing text column. It records `left_indent_pt`,
`right_indent_pt`, `first_line_offset_pt`, `label_indent_pt`, `label_alignment`, `label_gap_pt`,
paragraph `alignment`, `space_before_pt`, `space_after_pt` and `font_size_pt`.
A negative first-line offset represents a hanging indent.

| Field | Relationship |
| --- | --- |
| `paragraph_id` | Logical paragraph head ID, shared by its fragments; a new paragraph has a new ID. |
| `item_id/parent_item_id` | Item head and enclosing item head; item membership may include several paragraphs and equations. |
| `parent_id/container_role` | Semantic container and the unit's role, such as theorem, proof or exercise. |
| `section_id` | Nearest preceding outline heading head, derived in reading order. |

References resolve to existing heads. Container heads may refer to themselves; other parent
relationships cannot cycle. Paragraph fragments
agree on type, outline and membership. Only the head owns the label. Section membership supplies
heading context independently of paragraph, item and container grouping. Outline jumps are
retained and reported without inventing intermediate headings.

## Example

This is a paragraph-property fragment for complete source `(ii) The process is continuous.`;
the full SourceUnit also requires its identity, page, bounds, source binding and confidence.

```json
{
  "kind": "paragraph",
  "paragraph_id": "p0001-condition",
  "item_id": "p0001-condition",
  "paragraph": {
    "type": "numbered",
    "outline_level": null,
    "body_text": "The process is continuous.",
    "label": {
      "parts": [{"part_id": "condition-number", "source_text": "(ii)", "policy": "literal"}],
      "evidence": {"page": 1, "source_range": [0, 4]}
    },
    "layout": {"left_indent_pt": 24, "first_line_offset_pt": -24,
               "label_indent_pt": 0, "label_gap_pt": 4, "font_size_pt": 12}
  }
}
```
