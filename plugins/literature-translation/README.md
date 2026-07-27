# Literature Translation

`literature-translation` is a local Codex plugin for controlled, resumable translation of
English technical books and research papers into Simplified Chinese. Python manages stable
source units, exact LaTeX, structured tables, code, state, QA, reviews, and rendering. The
agent performs the language work; the package does not call a model API.

## First use

Requires Python 3.12 or later. From the plugin directory:

```powershell
python scripts/bootstrap.py
python scripts/littrans.py doctor
```

The launcher creates a private environment outside the plugin installation when necessary.
See `references/runtime.md` for launcher resolution from an installed Skill.

## Controlled workflow

1. Run `prepare-literature-translation` to initialize and extract a private project.
2. Run `verify-literature-extraction` and compare the visual overlay with every selected PDF
   page. Translation is blocked until formulas, tables, code, figures, notes, and paragraph
   boundaries are verified.
3. Run `translate-literature-section` on prepared batches. Every source unit is immutable;
   translations are separate revisioned records.
4. Run `audit-literature-translation` in an independent context. Reviewers write issue records,
   never the translation.
5. Run `finalize-literature-translation` to apply the machine-review gate and render Markdown
   plus responsive bilingual HTML. Human approval is never inferred.

Project state follows:

```text
extracted -> prepared -> draft -> qa-passed -> reviewed -> revised
          -> machine-reviewed -> human-approved
```

Only `machine-reviewed` or `human-approved` text may enter translation memory or a formal
render. Open blocker or major issues stop formal output.

## Formats and boundaries

- Display and inline mathematics are stored as reviewed LaTeX; crops are evidence only.
- Running heads, decorative separators, and other non-reading matter remain traceable source
  units but may use `render_policy: omit`; omitted units are neither batched nor rendered.
- `target_text` contains semantic body text only. The renderer owns heading, list, note,
  caption, and footnote wrappers, and deterministic QA rejects duplicated structural markup.
- Use `render --batch-id <id>` for a batch-exact artifact. Page rendering remains available
  for intentionally page-scoped collections and can include units from several batches.
- Tables are rectangular local structures and are translated cell by cell.
- Code retains exact whitespace and gains a language fence/highlighter when known.
- Figure images remain local; meaningful internal labels are translated alongside them.
- Reader notes are separate from the translation and require HTTPS sources plus an access date.
- The first release supports PDFs with a usable text layer. OCR, DOCX, MCP, and repaginated PDF
  output are intentionally out of scope.

Keep source PDFs, extracted assets, and translation workspaces outside version control. The
tool is intended for private research reading and does not determine publication rights.
