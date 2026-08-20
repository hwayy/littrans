# Literature Translation

`literature-translation` is an agent plugin for controlled, resumable translation of
English technical books and research papers into Simplified Chinese. It installs on Codex and
Cursor. Python manages stable source units, exact LaTeX, structured tables, code, state, QA,
reviews, and rendering. The agent performs the language work. The package does not call a model
API directly; projects may explicitly configure supported local CLIs for isolated, read-only
external review.

## First use

Requires Python 3.12 or later. From the plugin directory:

```powershell
python scripts/bootstrap.py
python scripts/littrans.py doctor
```

The launcher creates a private environment outside the plugin installation when necessary.
See `references/runtime.md` for launcher resolution from an installed skill, and
`references/host-runtimes.md` for Codex and Cursor invocation.

## Controlled workflow

1. Run `prepare-literature-translation` to initialize and extract a private project.
2. Run `verify-literature-extraction` and compare the visual overlay with every selected PDF
   page. Translation is blocked until formulas, tables, code, figures, notes, and paragraph
   boundaries are verified.
3. Run `translate-literature-section` on prepared batches. Every source unit is immutable;
   translations are separate revisioned records.
4. Run `audit-literature-translation` in an independent context. Reviewers write issue records,
   never the translation.
5. Optionally run configured external reviewers after machine review. Their evidence can grant
   `external-reviewed`, but never `human-approved`.
6. Run `finalize-literature-translation` to enforce the configured release gate and render
   Markdown plus responsive bilingual HTML. Human approval is never inferred.

For ongoing projects, `continue-literature-translation` freezes one consecutive same-stage wave.
Codex stays at three batches. Cursor defaults to six and may select up to nine. It uses compact
batch-local packets, fresh local writers and independent audit lenses, consolidated revisions,
and closure rechecks while retaining every quality gate. A second consolidated revision after
closure is expected when remaining fluency defects are accepted; do not skip that pass to stay
inside a typical-wave count. Do not use cloud or remote subagents for translation or review.

Claude stdin delivery remains disabled by the earlier shadow quality gate. The v0.5 minimal file
protocol is independently gated until it passes the six-batch quality-and-efficiency A/B;
production review otherwise retains the proven file packet protocol.

Useful v0.5 commands:

```text
littrans project migrate PROJECT --to 5 --dry-run
littrans project migrate PROJECT --to 5
littrans workflow next PROJECT
littrans workflow next PROJECT --host cursor
littrans workflow next PROJECT --host codex --limit 3
littrans workflow status PROJECT --batch-ids ID1,ID2,ID3
littrans workflow packet PROJECT --stage translate --batch-ids ID1,ID2,ID3
littrans workflow packet PROJECT --stage audit --lens all --batch-ids ID1,ID2,ID3
littrans review import-set PROJECT PACKET-MANIFEST ISSUES.jsonl
littrans workflow metrics PROJECT --batch-ids ID1,ID2,ID3
littrans workflow prune-packets PROJECT --dry-run
littrans render PROJECT --batch-id ID
```

Schema-v5 packets live under the ignored `.littrans/work` directory and are content-addressed for
reuse. Imported evidence remains authoritative; use `workflow prune-packets --apply` to remove only
work packets the CLI reports as safe. See `MIGRATING.md` before opening an older project with v0.5.

Project state follows:

```text
extracted -> prepared -> draft -> qa-passed -> reviewed -> revised
          -> machine-reviewed -> external-reviewed -> human-approved
```

Legacy projects may use `machine-reviewed` text in translation memory and formal renders.
Projects with external review enabled require `external-reviewed` or `human-approved`.
Open blocker or major issues stop formal output; external approval additionally requires no
open minor issues.

## Formats and boundaries

- Display and inline mathematics are stored as reviewed LaTeX; crops are evidence only.
- Running heads, decorative separators, and other non-reading matter remain traceable source
  units but may use `render_policy: omit`; omitted units are neither batched nor rendered.
- `target_text` contains semantic body text only. The renderer owns heading, list, note,
  caption, and footnote wrappers, and deterministic QA rejects duplicated structural markup.
- The regular translation wave renders one canonical single-batch artifact:
  `render --batch-id <id>`. `--name` defaults to the short batch key (`bNNN`) and
  overwrites `output/bNNN.*` only when the existing render-QA record belongs to the same batch.
  If another batch already owns that short name, pass an explicit unique `--name`.
- Keep `render --batch-ids` (one to nine consecutive batches) and `--pages` for later
  large-set or intentionally page-scoped collections. Combined rendering is not a required
  wave step.
- Tables are rectangular local structures and are translated cell by cell.
- Code retains exact whitespace and gains a language fence/highlighter when known.
- Figure images remain local; meaningful internal labels are translated alongside them.
- Reader notes are separate from the translation and require HTTPS sources plus an access date.
- The first release supports PDFs with a usable text layer. OCR, DOCX, MCP, and repaginated PDF
  output are intentionally out of scope.

Keep source PDFs, extracted assets, and translation workspaces outside version control. The
tool is intended for private research reading and does not determine publication rights.
