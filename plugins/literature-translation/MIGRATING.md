# Rebuilding a project for LitTrans 0.6

Version 0.6 uses schema 6 and a single fidelity-preservation workflow. Keep older projects intact; create a new project rather than upgrading extracted units or inheriting verification receipts.

```text
littrans project rebuild OLD NEW
```

`NEW` must be a new destination. Rebuild copies the source PDF, document brief, style guide and glossary. Old extraction, translations, batches, source verification, asset decisions, QA and approvals remain historical in `OLD`; none is treated as current v6 evidence. Confirm the new source fingerprint and copied context before preparing pages. Review role/external-service configuration explicitly where needed rather than inferring current credentials from historical output.

Run `source prepare`, inspect `source review-packets`, import source-bound visual review and run `source verify`. Create new batches, then dispatch independent transcribe and translate packets. A faithful original-image formula is ready for reading and translation while its structured candidate remains pending.

Older project schemas receive a rebuild instruction on write operations. Resume old work only with its recorded compatible plugin installation in a separate task; do not mix versions against one project. Checkpoint running tasks before changing the installed plugin, and retain caches they still use.

For QASC, keep the existing project as history and prepare an independent v6 project from its source/context. Experimental gold answers and historical model candidates are evaluation material, not input for new production workers.

After interruption, use status on the frozen new batch IDs, recover saved successful responses, import them idempotently and schedule only missing work. A pending LaTeX candidate does not reset a completed translation; a changed source or semantic dependency does require current review.

## Projects prepared with a 0.6.0 development build

- `context/chapters/` is no longer created; nothing ever read it, and an existing directory is
  harmless.
- Run `project scaffold PROJECT` (with `--repo-root DIR` for a nested layout) to add the record
  files a project created earlier lacks — `glossary/reference.yaml`, the handbook, records,
  ledger, launcher and `docs/LITTRANS.md`. Existing files are never overwritten; an existing
  `.gitignore` only gains `.littrans/*` and `!.littrans/work/`. Run it again with `--refresh`
  after every upgrade so `docs/LITTRANS.md` describes the installed build.
- `glossary/reference.yaml` is now read: it must hold a `terms` list (the same entry schema as
  `approved.yaml`, plus `kind` and `aliases`). A file in another shape fails to load with a
  clear error; convert it before creating packets. Adding reference entries changes the audit
  context of the batches whose units mention them, once.
- Paragraph white space now ends a paragraph (0.6.0-dev.8). A document that spaces its
  paragraphs instead of indenting them — reports, papers, anything whose flush blocks were
  merged into one unit per page — must be re-prepared with `source prepare --replace` and its
  pages reviewed again; the new units carry new IDs and no translation of the merged unit is
  reused. An indented book changes only on a page where prose opens after real paragraph
  white space (a theorem's conclusion after its enumerated clauses, say); every other page
  keeps its units, fingerprint and receipt. Detector-labelled `footnote` and `reference`
  blocks that were absorbed into prose become units of their own kind on re-preparation.

## Projects prepared with 0.6.0

No rebuild is needed. Re-preparing pages with `source prepare --replace` (recommended after
this update: negated relations such as `∉` now export precisely and change those units' source
hashes) also stops writing per-region `original.pdf` files, removes them from the directories
it re-exports and reclaims crop directories the new registry no longer references. Run
`source gc --dry-run` to list directories orphaned by earlier `--replace` runs, then
`source gc --apply` to remove them. Fragment records that still name a PDF stay valid until
their page is re-prepared.

