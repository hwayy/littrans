# Semantic extraction contract

## Required source representations

- Prose: one logical paragraph per unit; physical PDF line wraps are spaces.
- Continuation: cross-page or block continuations carry both continuation flags and render as one paragraph.
- Inline math: exact LaTeX between `$` delimiters in `source_markdown`; surrounding prose remains complete.
- Display math: exact `latex`, separate `equation_number`, and a retained crop used only for comparison.
- Table: rectangular `rows`, accurate `header_rows`, no screenshot fallback in final output.
- Code: exact characters and indentation, plus a known language when identifiable.
- Figure: original visual, translated caption, and translated internal labels or an explicit finding that none exist.
- Note/Tip/Warning: `note` kind so renderers emit an admonition.

## Override example

```yaml
overrides:
  - unit_id: p0003-u005-example
    latex: >-
      \mathbf{a}=\frac{\partial\mathbf{u}}{\partial t}
      +(\mathbf{u}\cdot\nabla)\mathbf{u}
    equation_number: "1"
    verified: true
    reason: Compared character-by-character with PDF p.3 at 200% zoom.
```

For tables, set `table.rows`, `table.header_rows`, and `table.column_count`. For figures, set `figure_labels` to objects containing `source` and `target`, then set `visual_text_status: verified`.
