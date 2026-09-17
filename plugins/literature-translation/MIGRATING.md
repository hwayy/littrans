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

## Projects prepared with 0.6.0-dev.8 or earlier: page-scoped guidance and layout evidence (0.6.0-dev.9)

- **Receipts that failed with `source structure guidance changed since review` because the
  profile file was reformatted or checked out with other line endings verify again** without
  any review: the comparison reads the profile's content, per page.
- **A profile whose base `handling_rules` were extended for a later chapter.** Receipts of
  the earlier chapter are bound to the shorter text, receipts of the later chapter to the
  longer. Restore each base rule to the text the earlier packets embed (`git show
  <commit>:context/source-structure.json`, or the `document_structure.profile.handling_rules`
  of one of their `packets/source-*/packet.json`) and move the appended lines into one block
  `page_rules: [{"label": "Chapter 2", "pages": "52-67", "handling_rules": {"lists":
  "- Chapter 2 …", …}}]`. When the lines were appended after a line break, both chapters'
  receipts verify: a base rule split at a line boundary into a scoped block reads the same.
  `source verify --pages …` names any page and key that still differs. From now on, put a
  new scope's rules in a `page_rules` block; editing a base rule voids every receipt.
- **Older plugin builds refuse a profile with `page_rules`** (`extra` fields are forbidden);
  upgrade every host that shares the project before adding one.
- **A page re-prepared after the upgrade** records `structure.document_profile.guidance_sha256`
  instead of `sha256` in its ledger, so the first `--replace` of a reviewed page under a
  profile changes that page's fingerprint once and needs a fresh receipt; pages that are not
  re-prepared keep theirs. On a page whose page number is detached from the running head, a
  re-preparation also moves an unreferenced figure to its reading position (next to its
  caption) and an omitted running-head rule to the top of the unit order, and a paragraph that
  follows a list item's continuation line becomes a unit of its own; measured on a 16-page
  chapter, every page's unit order changed for the rule alone while units, text and reading
  output changed only on the two pages with those forms. Re-prepare a reviewed chapter only
  for a page that needs the fix, and review it from a fresh packet.
- **Layout results join the record.** In a project's `.gitignore` replace
  `derived/fidelity-layout/` with `derived/fidelity-layout/*.request.json` and
  `derived/fidelity-layout/*.log`, then commit the `derived/fidelity-layout/<fingerprint>.json`
  files that exist (`project tracked` lists them until they are committed). A host that never
  ran the detector can then re-prepare every recorded page, and a page re-prepared alone
  (`--replace --pages 30`) keeps its layout fingerprint and receipt. A result that only ever
  existed on another host stays missing there until that host commits it; until then a
  `--replace` of such a page detects it afresh (with the layout runtime) and reports it in
  `detected_layout_pages`.
- **`source prepare --replace` reuses recorded results by default.** Pass `--redetect` for
  the previous behaviour (a fresh detection of every page) when the detector was upgraded on
  purpose.
- **The scaffolded launcher.** `tools/lt.py` is user-owned and is not rewritten: to get the
  cross-host resolution, delete `tools/lt.py` and run `project scaffold PROJECT` (with
  `--repo-root DIR` for a nested layout), commit it, and give `tools/lt.sh` its executable
  bit once (`git update-index --chmod=+x tools/lt.sh` from Windows, `chmod +x` on POSIX).
  Likewise the generated `.gitattributes` is not rewritten; add `*.cmd text eol=crlf`,
  `*.sh text eol=lf` and `*.py text eol=lf` by hand if the repository is checked out on both
  platforms.
- **`project.yaml` written by `project init` before this build** may hold an absolute
  `source_path`. Edit it to the project-relative `source/<name>.pdf` (and copy the PDF there)
  so a clone on another host finds the PDF.
- **Rendered checkpoints** now link relatively: a `source render` or `finalize` output made
  before the upgrade differs from a new one by its `file:` links only.

## Projects prepared with 0.6.0

No rebuild is needed. Re-preparing pages with `source prepare --replace` (recommended after
this update: negated relations such as `∉` now export precisely and change those units' source
hashes) also stops writing per-region `original.pdf` files, removes them from the directories
it re-exports and reclaims crop directories the new registry no longer references. Run
`source gc --dry-run` to list directories orphaned by earlier `--replace` runs, then
`source gc --apply` to remove them. Fragment records that still name a PDF stay valid until
their page is re-prepared.

