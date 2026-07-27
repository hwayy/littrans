# Semantic extraction contract

## Required source representations

- Prose: one logical paragraph per unit; physical PDF line wraps are spaces.
- Continuation: cross-page or block continuations carry both continuation flags and render as one paragraph.
- Inline math: exact LaTeX between `$` delimiters in `source_markdown`; surrounding prose remains complete.
- Display math: exact `latex`, separate `equation_number`, and a retained crop used only for comparison.
- Table: rectangular `rows`, accurate `header_rows`, no screenshot fallback in final output.
- Code: exact characters and indentation, plus a known language when identifiable.
- Figure: original visual, translated caption, and translated internal labels or an explicit finding that none exist.
- Note/Tip/Warning/Caution/“What’s New”: `note` kind plus explicit `callout_kind` so renderers emit the correct localized admonition label. Source-prefix inference is a legacy fallback only.
- Titled sidebar: retain separate heading/body units, assign a shared `sidebar_id`, mark the heading as `sidebar_role: title`, mark every contained unit as `sidebar_role: body`, and verify the entire group visually. Do not flatten a multi-paragraph sidebar into an ordinary heading plus body prose.

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

For tables, set `table.rows`, `table.header_rows`, and `table.column_count`. One logical table remains one structured table across physical pages; include every body row, preserve empty cells, omit absorbed paragraph fragments as duplicates, and keep one evidence crop per physical page region. For figures, set `figure_labels` to objects containing `source` and `target`, then set `visual_text_status: verified`.
