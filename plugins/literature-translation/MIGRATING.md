# Migrating to LitTrans 0.7

This guide takes an existing project to 0.7 (currently the development build `0.7.2-dev.2`). Find the version that last wrote the project
(`plugin_version` in `derived/provenance.json`, or the `generator` block of a page ledger or
packet; `littrans doctor` prints the installed build), then follow the section for it.

| Project written by | Schema | What to do |
| --- | --- | --- |
| 0.5.x or earlier | 5 or older | [Rebuild into a new project](#from-05-or-earlier-rebuild) |
| 0.6.0, 0.6.2, any `0.6.0-dev.N` or `0.6.1-dev.N` | 6 | [Upgrade in place](#from-a-06-build-upgrade-in-place) |

The repository's [CHANGELOG.md](../../CHANGELOG.md) describes each development build in detail. Where a step
below applies only to older builds, it names the first build that no longer needs it.

## Before upgrading

- **Checkpoint running tasks.** Let running agents finish or reach a durable checkpoint, and
  save successful responses before import. Do not remove the old plugin cache while a task may
  still call its scripts, templates or schemas.
- **Upgrade every host that shares the project.** Hosts that share a record through git must
  run the same build (`doctor` reports `build.plugin_version` and `build.build_digest`). Builds
  before `0.6.0-dev.9` refuse a structure profile that has `page_rules`. Pages re-prepared on
  different builds may be cut differently.
- **Do not mix versions against one project.** Resume old work only with its recorded plugin
  installation, in a separate task.

## From 0.5 or earlier: rebuild

0.6 uses schema 6 and a single fidelity-first workflow. Older projects are not upgraded in
place: extracted units, translations, batches, verification receipts and approvals do not carry
over. Keep the old project intact as history and create a new one from it:

```text
littrans project rebuild OLD NEW
```

`NEW` must not exist yet. Rebuild copies the source PDF, document brief, style guide, glossary
and `docs/` into it. It also grows the project record: launcher, handbook, `.gitignore` and
`docs/LITTRANS.md`. Old extractions, translations, batches, source verification, asset
decisions, QA and approvals stay in `OLD`; none counts as current evidence. Write operations
on an older project tell you to rebuild it.

1. Confirm the new source fingerprint and the copied context. Review `agent_models` in the new
   `project.yaml` explicitly (`project models NEW --host HOST`). Do not assume credentials or
   model choices from historical output.
2. Probe and prepare: `source probe`, complete `context/source-structure.json`, then
   `source prepare`.
3. Verify with the `prepare-literature-source` skill: `literature-source-reviewer` subagents
   review, correct and import page ranges, then `source verify` must pass.
4. When the user asks to start translating, create new batches (`batch create`). Then dispatch independent transcribe and translate
   packets. A formula with a faithful original image is ready for reading and translation
   while its structured candidate is still pending.

Experimental gold answers and historical model candidates in the old project are material
for evaluation, not input for new production workers.

## From a 0.6 build: upgrade in place

Schema-6 projects need no rebuild. What the project has recorded — pages, assets, overrides,
receipts, translations and approvals — stays valid, apart from the cases listed in step 4.
Re-preparation, the only step that changes how a page is read, is optional and applies only to
the pages you choose.

### 1. Install and check

Install 0.7.2-dev.2 on every host (see the repository README) and start a new agent session.
Check that `littrans doctor` reports `0.7.2-dev.2`. The skills `prepare-literature-translation`
and `verify-literature-extraction` are gone: invoke `prepare-literature-source` instead, which
dispatches source review to `literature-source-reviewer` subagents. Update any project notes
(`AGENTS.md`, `CLAUDE.md`, handbook) that name the old skills.

**Windows: the cache moved (`0.7.1-dev.1`).** The CLI and layout environments now live in
`%USERPROFILE%\.littrans` instead of `%LOCALAPPDATA%\littrans`, which a packaged client
(Codex) sees redirected ([runtime.md](references/runtime.md#packaged-windows-clients)). The
CLI environment rebuilds itself on first use (package-index access). Then, from an ordinary
terminal rather than a Codex session, run on each Windows host:

```text
littrans layout install
```

It copies the detector weights from the old cache when the old ready receipt verifies them,
so only PyTorch and MinerU are downloaded again, and builds a fresh environment. When
`doctor` reports `layout_runtime.ok` on the new location, delete the directory it names as
`legacy_cache`. Pages already prepared keep their recorded layout results; a new runtime only
changes the fingerprint of detections made from now on. Set `LITTRANS_CACHE_DIR` to keep the
cache elsewhere, outside `AppData`.

### 2. Bring the project record up to date

1. **Add missing record files.** Run:

   ```text
   littrans project scaffold PROJECT --refresh
   ```

   For a nested project, add `--repo-root DIR` once; the project then remembers its record
   root. The command creates only missing files, never overwrites user-owned ones and
   regenerates the plugin-owned `docs/LITTRANS.md`. Projects created before `0.6.0-dev.7`
   receive the whole record structure here: `glossary/reference.yaml`, handbook, records,
   defect ledger, launcher and `docs/LITTRANS.md`. Run it with `--refresh` after every future
   upgrade too.
2. **Regenerate the launcher** if `tools/lt.py` was generated before `0.6.1-dev.5`. The
   launcher is user-owned, so scaffolding never replaces it. Older launchers can run a stale
   cached build instead of the current client's install, and cannot use an active virtual
   environment or a wheel-only installation. Move `tools/lt.py`, `tools/lt.cmd` and
   `tools/lt.sh` out of the record (keep them as a backup), then run `project scaffold PROJECT`
   again. Reapply any local customizations from the backup, and commit. On Windows, give
   `tools/lt.sh` its executable bit once:
   `git update-index --chmod=+x tools/lt.sh`. Until you regenerate the launcher, set
   `LITTRANS_PLUGIN_ROOT` to the installed plugin directory to override the old one.
3. **Check `.gitignore` and `.gitattributes`** if they were generated before `0.6.0-dev.9`.
   Both files are user-owned, and scaffolding only adds the `.littrans/*` /
   `!.littrans/work/` pair to `.gitignore`.
   - In `.gitignore`, replace `derived/fidelity-layout/` with
     `derived/fidelity-layout/*.request.json` and `derived/fidelity-layout/*.log`: layout
     results are part of the record. Remove a leftover whole-directory `/.littrans/` line if
     it is still there, and one of two identical `.littrans/*` pairs.
   - In `.gitattributes`, if the repository is checked out on both Windows and POSIX, add
     `*.cmd text eol=crlf`, `*.sh text eol=lf` and `*.py text eol=lf`.
4. **Make `project.yaml` portable** if `project init` ran before `0.6.0-dev.9`. An absolute
   `source_path` becomes the project-relative `source/<name>.pdf`; copy the PDF there on
   every host.
5. **Check the reference glossary.** `glossary/reference.yaml` must hold a `terms` list (the
   `approved.yaml` entry schema plus `kind` and `aliases`); convert a file in another shape.
   Run `littrans glossary check PROJECT` to report schema errors and entries that match no
   prepared unit.
6. **Confirm the record.** Run `littrans project tracked PROJECT` and commit until it exits
   0. It requires `project.yaml`, `derived/provenance.json` (restore a deleted provenance
   from git history), every layout result the ledgers name and every source packet a page
   receipt names.

### 3. Check the configuration

**Dispatch roles (0.7).** Every model stage now runs in a subagent, and source review has a
role of its own. Existing projects keep working without an edit: a role they leave unset
dispatches on the host's default and is reported as an advisory. To adopt the 0.7
recommendations, edit `agent_models` in `project.yaml`:

```yaml
agent_models:
  codex:
    translate:     {model: gpt-6-luna, reasoning_effort: max}
    transcribe:    {model: gpt-6-luna, reasoning_effort: max}
    audit:         {model: gpt-6-sol,  reasoning_effort: high}
    asset-audit:   {model: gpt-6-sol,  reasoning_effort: high}
    source-review: {model: gpt-6-sol,  reasoning_effort: high}
  claude:
    translate:     {model: sonnet}
    transcribe:    {model: sonnet}
    audit:         {model: sonnet}
    asset-audit:   {model: sonnet}
    source-review: {model: sonnet}
```

Remove every `reasoning_effort` under `agent_models.claude` (0.6 projects carry `high` for
`translate` and `transcribe`). Claude Code takes no per-dispatch effort, so the LitTrans agents
fix it in their frontmatter (`effort: high`); a configured Claude effort is not applied, and
every dispatch now reports it as an advisory. Replace `gpt-5.6-luna` with `gpt-6-luna` on
Codex. Packets created after the edit record the new policy; packets already dispatched keep
theirs.

**Earlier builds.** The old flat `agent_models.<host>` form (one model string per
role, one shared `reasoning_effort`) is still read and is rewritten in the per-role form
(`translate: {model: sonnet, reasoning_effort: high}`) on the next save. A misspelled host or
role key now fails loudly instead of being ignored; the roles are `translate`, `transcribe`,
`audit`, `asset-audit` and `source-review`. An unset model or effort dispatches on the host's own default, so
Cursor and Qoder projects that could not create packets before `0.6.0-dev.16` now create them.
Run `littrans project models PROJECT --host HOST` to see the resolved policy and any
advisories.

### 4. Settle existing evidence

These cases need action once. Everything else keeps passing.

- **In-flight packets.** If you upgrade from a build before `0.6.0-dev.16`, finish or
  discard the translate, revise and asset-audit packets you dispatched. Their identities now
  include the per-role dispatch policy. Recreated packets get new IDs, so a worker that returns
  with an old `packet_id` fails its binding.
- **Deterministic QA.** The QA version changed (last in `0.6.1-dev.9`). Every batch's
  `qa/<batch>.json` reads as stale until you run `littrans qa run PROJECT BATCH` again.
  Translations and receipts are untouched.
- **Audits reset once.** Audit context now includes reference terminology (`0.6.0-dev.7`) and
  terms inside non-title quotations (`0.6.1-dev.9`). Batches whose units mention such
  entries report `context-changed` once and need a new audit round.
  `littrans glossary lookup PROJECT --pages …` shows what a batch receives.
- **Source receipts that no longer verify.** `source verify` names these pages:
  - *`source structure guidance changed since review`* from reformatting or line endings
    alone clears without review, because receipts compare each page's guidance by content.
    If a base `handling_rules` entry was extended in place for a later chapter, restore it
    with a packet the earlier chapter's receipts name. `source verify` lists these packets in
    `receipt_packets`.

    ```text
    littrans source rescope PROJECT --packet source-<id> --pages 52-67 --label "Chapter 2" [--dry-run]
    ```

    This moves the appended lines into a `page_rules` block. A rule rewritten rather than
    extended must be edited by hand. From now on, put a new scope's rules in `page_rules`;
    editing a base rule voids every receipt.
  - *An approved page with an unaccepted pending grouping decision*: re-review the page from
    a fresh packet. A pending asset must be listed in `accepted_grouping_pending` as an
    `{"asset_id", "reason"}` object; a legacy review with bare strings must be re-imported.
  - *A missing receipt packet*: restore `packets/source-<id>/` from git. Packets that
    receipts name are part of the record.
- **Review tooling.** Source review packets made by `0.6.1-dev.2` or later list
  `context.structure_checks`. A decision must copy each `roles` row into `confirmed_roles`
  (`{"unit_id", "kind"}`, the kind read on the original) and each `joins` row into
  `confirmed_joins` (`{"block"}`). Otherwise the page is rejected with `role-unconfirmed` or
  `join-unconfirmed`. Correct a disputed kind with a `units` override instead of confirming it.
  Override documents must carry every block the page's ledger records (`regions`, `units`,
  `page_canvas_bbox`) forward, or state `"units": null` to drop one.
- **Workflow coordination** no longer refuses a project whose later chapters are prepared but
  not batched. `workflow status` and `workflow next` report such pages as `unbatched_pages`.
  There is no need to batch every chapter just to query progress.

### 5. Re-prepare pages only where the new reading is wanted

Nothing about a page changes until it is re-prepared. Re-prepare a reviewed chapter only for
the pages that need a fix, and review those pages again from fresh packets:

```text
littrans source prepare PROJECT --replace --pages 30,41-43
littrans source review-packets PROJECT --pages 30,41-43
```

- **Layout results are reused.** `--replace` cuts every page on the layout result its ledger
  records, so no layout runtime is needed for recorded pages. Pass `--redetect` only after an
  intentional detector upgrade.
- **Recorded corrections replay.** A page with a reviewer's `units` or `regions` override
  replays it as recorded. To adopt the new native reading on such a page, pass
  `--discard-overrides` for that page alone. If an override names an asset whose ID moved, the
  replay is refused with both IDs named (`not cut on this page any more: <old id>; cut but
  unreferenced: <new id>`). Either re-import the review with the new ID or discard the
  override for that page. The whole transaction rolls back, so no page is half-replaced.
- **Fingerprints move once.** A re-prepared page from an older build usually gets a new
  fingerprint even if its text is unchanged: build identity, per-page guidance hash and
  override hashing changed. It then needs a fresh receipt. Since `0.7.1-dev.1`,
  `invalidated_pages` names every page whose receipt the run removed, the re-prepared pages
  included (earlier builds listed only pages outside the run: derive those from
  `prepared_pages` minus `retained_receipt_pages`). A page whose ledger records an older
  guidance digest while its receipt was reviewed under the current guidance keeps its receipt.
  Pages whose units or structure metadata a newer build computes differently still lose
  theirs.
- **Documents that space rather than indent their paragraphs** (reports, papers) prepared
  before `0.6.0-dev.8` had each page merged into one unit. Re-prepare them entirely and review
  every page again. The new units have new IDs, and translations of the merged units are not
  reused. Indented books change only where prose opens after real paragraph white space.
- **Crops.** Re-preparation stops writing per-region `original.pdf` files and removes them
  from the directories it re-exports. Run `littrans source gc PROJECT --dry-run`, then
  `--apply`, to remove crop directories that earlier `--replace` runs orphaned.

What re-preparation reads differently, in outline (the changelog has each rule):

- **Inline notation:** word spaces at asset edges follow the printed ink; operator names
  (`log`, `lim`, `Cov(`), exponents of text-face numbers (`2^{19937}`), closing brackets on
  the next line, accents over math letters and cases blocks in running text stay in one asset.
  A closing `!` is a factorial unless the sentence ends there.
- **Displays:** printed equation labels leave the crop and bind as `equation_number`; one
  detector box over two labelled displays becomes two units; numerators, case rows and
  connectives that hold words stay in the display as declared conditions. Since
  `0.7.2-dev.1`, the rows of a display split around its own label line are one asset, and a
  label line set just above or below its display binds to it.
- **Figures (`0.7.2-dev.1`):** the panels of a composite figure under one caption are one
  asset with a fragment per panel.
- **Structure:** list labels, list continuations, paragraph white space, statement
  continuations, proof tombstones and page-top continuation flags follow the page's
  typography; since `0.7.2-dev.1` the flags skip a figure, table or caption at the page edge.
  Detector heading and caption labels no longer decide a kind on their own. Table boxes grow
  over their header rows.

### 6. Re-render

Re-render editions (`render`) and source checkpoints (`source render`) to pick up relative
links, page-spanning paragraph merging in Markdown and HTML, and translated language in
equation lines. No record changes.

## After an interruption

Ask `workflow status` about the frozen batch IDs, recover the successful responses you saved,
and import them; imports are idempotent. Then dispatch only the missing work. A pending LaTeX
candidate does not reset a completed translation. A changed source unit or semantic
dependency does require a current review.
