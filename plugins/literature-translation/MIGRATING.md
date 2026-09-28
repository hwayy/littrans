# Migrating to LitTrans

## 0.8.2-dev.1: Codex task isolation and path resolution

Schema-6 projects upgrade in place. Run `project agents PROJECT --host codex --check`
and then `--write`, preserving any previous `--workspace` selection. Managed definitions
now prefer the task's saved role, resolve fallback paths from the agent file and request
disabling nested delegation. Host enforcement must be verified. User-edited files still
report conflicts rather than being overwritten.
Restart the host if needed to discover the regenerated definitions.

Ordinary Codex subagents need no generated agents. Follow [Codex](references/host-codex.md)
to explicitly choose a fresh context and the task's configured model/effort. Existing tasks
keep their snapshots; create fresh handoffs when adopting updated instructions. OpenCode
model policies and permissions retain the 0.8.1 behavior. No source rebuild is required.

## 0.8.1: OpenCode 2.x native configuration

Schema-6 projects upgrade in place. Existing model policies, source data and approvals are
preserved. New projects get OpenCode defaults matching Codex with the `openai/` provider prefix.
For an existing project, copy the `opencode` section of `profiles/host-models.yaml` into
`project.yaml` under `agent_models` only if you want those defaults. An existing empty policy
continues to inherit; no automatic migration changes its model choice.

Run `project agents PROJECT --host opencode --check` and then `--write` with the same
`--workspace` used previously. Unmodified managed definitions upgrade to V2 `permissions:`
and the project's `provider/model#variant` selectors. If files were manually changed, back
them up and move the conflicting files out of `.opencode/agents/` before regenerating; reconcile
customizations explicitly. Do not combine legacy `permission:` with the V2 rules.

Start a fresh OpenCode session in the project/workspace, verify native agents and their model
selectors, and pass absolute task handoffs when dispatching. OpenCode 1.x is not supported by
the generated 0.8.1 adapter. The main session's model remains an OpenCode setting; new top-level
CLI sessions require explicit model selection. See [OpenCode](references/host-opencode.md).
Regenerate task handoffs when adopting the new path guidance; do not edit bound snapshots of
running tasks. Completed source/translation data does not require re-extraction solely for this fix.

## 0.8.0-dev.1: canonical CLI entries

The first 0.8 development build adds domain command groups. Existing commands remain
compatible throughout 0.8.x and print a deprecation hint on stderr; stdout and exit codes
keep their contract. See [the complete command mapping](references/cli-migration.md).
No source rebuild, project schema migration or historical packet rewrite is needed for
this routing change. The six former skills are replaced by four coordinator skills;
worker guidance now lives in roles/. Restart the host session to refresh discovery.
Optional project agents are generated explicitly with project agents --write; user edits
are preserved. Unknown or mixed host environments now resolve to generic, so pass --host
when the host cannot be detected (including OpenCode). Existing project role policies remain
intact. Task envelopes use protocol 1 without changing project schema 6; their instruction
snapshots and context bindings are additional execution records, not new approval levels.

## Upgrading older projects to 0.8

This guide takes an existing project to 0.8.2-dev.1. Find the version that last wrote the project
(`plugin_version` in `derived/provenance.json`, or the `generator` block of a page ledger or
packet; `littrans doctor` prints the installed build), then follow the path for it.

| Project last written by | Schema | What to do |
| --- | --- | --- |
| 0.5.x or earlier | 5 or older | [Rebuild into a new project](#from-05-or-earlier-rebuild) |
| Any 0.6 or 0.7 build, or an earlier 0.8 development build | 6 | [Upgrade in place](#upgrade-in-place) |

The repository's [CHANGELOG.md](../../CHANGELOG.md) describes each development build in detail.

## Before upgrading

- **Checkpoint running tasks.** Let running agents finish or reach a durable checkpoint, and
  save successful responses before import. Do not remove the old plugin cache while a task may
  still call its scripts, templates or schemas.
- **Upgrade every host that shares the project.** Hosts that share a record through git must
  run the same build (`doctor` reports `build.plugin_version` and `build.build_digest`). Pages
  re-prepared on different builds may be cut differently.
- **Do not mix versions against one project.** Resume old work only with its recorded plugin
  installation, in a separate task.

## From 0.5 or earlier: rebuild

Since 0.6, LitTrans uses schema 6 and a single fidelity-first workflow. Older projects are not
upgraded in place: extracted units, translations, batches, verification receipts and approvals
do not carry over. Keep the old project intact as history and create a new one from it:

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
   `source extract`.
3. Verify with the `source-processor` skill: independent source-review workers
   return decisions for the coordinator to receive, then `source verify` must pass.
4. When you ask to translate, `translation-coordinator` cuts batches for the pages and
   dispatches independent transcribe and translate packets. A formula with a faithful original
   image is ready for reading and translation while its structured candidate is still pending.

Experimental gold answers and historical model candidates in the old project are material
for evaluation, not input for new production workers.

## Upgrade in place

Schema-6 projects need no rebuild. What the project has recorded — pages, assets, overrides,
receipts, translations and approvals — stays valid, apart from the cases step 6 lists.
Re-preparation, the only step that changes how a page is read, is optional and applies only to
the pages you choose.

Take the steps the column of your build marks, in order. Within a step, an item that names a
build applies only to projects written before that build.
For 0.7.6 and later, follow steps 1, 2 and 4; the remaining historical fixes apply only when
the named older evidence is still present. The 0.8 command/skill reorganization alone does
not require re-extraction or re-approval.

| Step | 0.6.0, `0.6.0-dev.N`, `0.6.1-dev.N` | 0.6.2 | `0.7.0-dev.1` | `0.7.1-dev.N` | `0.7.2-dev.N` |
| --- | --- | --- | --- | --- | --- |
| [1. Install and check](#1-install-and-check) | yes | yes | yes | yes | yes |
| [2. Switch to the 0.8 skills](#2-switch-to-the-08-skills) | yes | yes | yes | yes | yes |
| [3. Move the Windows cache](#3-move-the-windows-cache) | Windows | Windows | Windows | — | — |
| [4. Bring the record up to date](#4-bring-the-record-up-to-date) | yes, with older-record items | yes | yes | yes | yes |
| [5. Update the dispatch configuration](#5-update-the-dispatch-configuration) | yes | yes | — | — | — |
| [6. Settle existing evidence](#6-settle-existing-evidence) | yes | check | check | check | check |
| [7. Re-prepare where wanted](#7-re-prepare-pages-only-where-the-new-reading-is-wanted) | optional | optional | optional | optional | optional |
| [8. Re-render](#8-re-render) | yes | after step 7 | after step 7 | after step 7 | after step 7 |

### 1. Install and check

Install 0.8.1 on every host that works on the project (see the repository README). Then start
a new agent session on each host: a running session keeps the skills and agents it loaded.
Check that `littrans doctor` reports the selected version and the same `build.build_digest` everywhere.

### 2. Switch to the 0.8 skills

- **Source preparation.** Invoke `source-processor` for extraction and optional asset parsing. It
  dispatches page review to fresh workers following `roles/source-reviewer.md`. Workers return
  decisions and proposed `page_rules`; the coordinator receives results and records validated rules
  in the structure profile. Update any project notes (`AGENTS.md`, `CLAUDE.md`, handbook) that
  name the old skills.
- **Subagents.** Every model stage runs in a fresh subagent of its stage's
  agent. Only a host without subagents falls back to a separate fresh session.
- **Batches.** `source-processor` ends with the verified source and the
  `source render` checkpoint; it does not create batches. `context-manager` owns shared context.
  `translation-coordinator` cuts batches when you ask to translate pages that no batch
  covers yet. Existing batches are unaffected.

### 3. Move the Windows cache

Builds before `0.7.1-dev.1` kept the CLI and layout environments in `%LOCALAPPDATA%\littrans`,
which a packaged client (Codex) sees redirected ([runtime.md](references/runtime.md#packaged-windows-clients)).
They now live in `%USERPROFILE%\.littrans`. The CLI environment rebuilds itself on first use
(package-index access). Then, from an ordinary terminal rather than a Codex session, run on
each Windows host:

```text
littrans layout install
```

It copies the detector weights from the old cache when the old ready receipt verifies them,
so only PyTorch and MinerU are downloaded again, and builds a fresh environment. When
`doctor` reports `layout_runtime.ok` on the new location, delete the directory it names as
`legacy_cache`. Pages already prepared keep their recorded layout results; a new runtime only
changes the fingerprint of detections made from now on. Set `LITTRANS_CACHE_DIR` to keep the
cache elsewhere, outside `AppData`.

### 4. Bring the record up to date

1. **Refresh the record files.** Run:

   ```text
   littrans project scaffold PROJECT --refresh
   ```

   For a nested project, add `--repo-root DIR` once; the project then remembers its record
   root. The command creates only missing files, never overwrites user-owned ones and
   regenerates the plugin-owned `docs/LITTRANS.md`, which states the installed build's roles
   and guarantees. Projects created before `0.6.0-dev.7` receive the whole record structure
   here: `glossary/reference.yaml`, handbook, records, defect ledger, launcher and
   `docs/LITTRANS.md`. Run it with `--refresh` after every future upgrade too.
2. **Older-record items.** Projects last written by a 0.6 build before 0.6.2 may also need:
   - *The launcher*, if `tools/lt.py` was generated before `0.6.1-dev.5`. It is user-owned,
     so scaffolding never replaces it. Older launchers can run a stale cached build instead of
     the current client's install, and cannot use an active virtual environment or a
     wheel-only installation. Move `tools/lt.py`, `tools/lt.cmd` and `tools/lt.sh` out of the
     record (keep them as a backup), run `project scaffold PROJECT` again, reapply any local
     customizations from the backup, and commit. On Windows, give `tools/lt.sh` its executable
     bit once: `git update-index --chmod=+x tools/lt.sh`. Until you regenerate the launcher,
     set `LITTRANS_PLUGIN_ROOT` to the installed plugin directory to override the old one.
   - *`.gitignore` and `.gitattributes`*, if they were generated before `0.6.0-dev.9`. Both
     files are user-owned, and scaffolding only adds the `.littrans/*` / `!.littrans/work/`
     pair to `.gitignore`. In `.gitignore`, replace `derived/fidelity-layout/` with
     `derived/fidelity-layout/*.request.json` and `derived/fidelity-layout/*.log`: layout
     results are part of the record. Remove a leftover whole-directory `/.littrans/` line and
     one of two identical `.littrans/*` pairs. In `.gitattributes`, if the repository is
     checked out on both Windows and POSIX, add `*.cmd text eol=crlf`, `*.sh text eol=lf` and
     `*.py text eol=lf`.
   - *A portable `project.yaml`*, if `project init` ran before `0.6.0-dev.9`. An absolute
     `source_path` becomes the project-relative `source/<name>.pdf`; copy the PDF there on
     every host.
   - *The reference glossary.* `glossary/reference.yaml` must hold a `terms` list (the
     `approved.yaml` entry schema plus `kind` and `aliases`); convert a file in another shape.
     `littrans glossary check PROJECT` reports schema errors and entries that match no
     prepared unit.
3. **Confirm the record.** Run `littrans project tracked PROJECT` and commit until it exits
   0. It requires `project.yaml`, `derived/provenance.json` (restore a deleted provenance
   from git history), every layout result the ledgers name and every source packet a page
   receipt names.

### 5. Update the dispatch configuration

Since `0.7.0-dev.1`, source review has a dispatch role of its own. Existing projects keep
working without an edit: a role they leave unset dispatches on the host's default and is
reported as an advisory. To adopt the 0.7 recommendations, edit `agent_models` in
`project.yaml`:

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

- Remove every `reasoning_effort` under `agent_models.claude` (0.6 projects carry `high` for
  `translate` and `transcribe`). Claude Code takes no per-dispatch effort, so the LitTrans
  agents fix it in their frontmatter (`effort: high`); a configured Claude effort is not
  applied, and every dispatch reports it as an advisory until you remove it.
- Replace `gpt-5.6-luna` with `gpt-6-luna` on Codex.
- The old flat `agent_models.<host>` form of builds before `0.6.0-dev.16` (one model string
  per role, one shared `reasoning_effort`) is still read and is rewritten in the per-role form
  on the next save. A misspelled host or role key fails loudly; the roles are `translate`,
  `transcribe`, `audit`, `asset-audit` and `source-review`.

Packets created after the edit record the new policy; packets already dispatched keep theirs.
Run `littrans project models PROJECT --host HOST` to see the resolved policy and any
advisories.

### 6. Settle existing evidence

Run `littrans source verify PROJECT` and `littrans workflow status PROJECT --batch-ids …` for
the batches in progress. They name every page and batch that needs action. From 0.6.2 or a 0.7
build nothing below is expected; a page `source verify` names still needs a fresh review
packet. Projects written by an earlier 0.6 build settle these cases once; everything else keeps
passing.

- **In-flight packets** (before `0.6.0-dev.16`). Finish or discard the translate, revise and
  asset-audit packets you dispatched. Their identities now include the per-role dispatch
  policy, so a worker that returns with an old `packet_id` fails its binding.
- **Deterministic QA** (before `0.6.1-dev.9`). Every batch's `qa/<batch>.json` reads as stale
  until you run `littrans translation qa PROJECT BATCH` again. Translations and receipts are
  untouched.
- **Audits reset once** (before `0.6.1-dev.9`). Audit context includes reference terminology
  (`0.6.0-dev.7`) and terms inside non-title quotations (`0.6.1-dev.9`). Batches whose units
  mention such entries report `context-changed` once and need a new audit round.
  `littrans glossary lookup PROJECT --pages …` shows what a batch receives.
- **Source receipts that no longer verify.**
  - *`source structure guidance changed since review`* from reformatting or line endings
    alone clears without review, because receipts compare each page's guidance by content.
    If a base `handling_rules` entry was extended in place for a later chapter, restore it
    with a packet the earlier chapter's receipts name. `source verify` lists these packets in
    `receipt_packets`.

    ```text
    littrans source rescope PROJECT --packet source-<id> --pages 52-67 --label "Chapter 2" [--dry-run]
    ```

    This moves the appended lines into a `page_rules` block. A rule rewritten rather than
    extended must be edited by hand. Put a new scope's rules in `page_rules`; editing a base
    rule voids every receipt.
  - *An approved page with an unaccepted pending grouping decision*: re-review the page from
    a fresh packet. A pending asset must be listed in `accepted_grouping_pending` as an
    `{"asset_id", "reason"}` object; a legacy review with bare strings must be re-imported.
  - *A missing receipt packet*: restore `packets/source-<id>/` from git. Packets that
    receipts name are part of the record.
- **Review packets** made before `0.6.1-dev.2` carry no `structure_checks`, so their receipts
  keep passing. Every new packet asks the reviewer to confirm each role and join it lists; the
  `literature-source-reviewer` agent follows
  [source-review.md](references/source-review.md).

### 7. Re-prepare pages only where the new reading is wanted

Nothing about a page changes until it is re-prepared. Re-prepare a reviewed chapter only for
the pages that need a fix, and review those pages again from fresh packets:

```text
littrans source extract PROJECT --replace --pages 30,41-43
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
- **Receipts.** A re-prepared page keeps its receipt only when its fingerprint is unchanged. A
  page prepared on an older build usually gets a new fingerprint even if its text is
  unchanged (build identity, per-page guidance hash and override hashing changed during 0.6),
  and pages whose units or structure metadata a newer build computes differently lose theirs.
  `invalidated_pages` names every page whose receipt the run removed, the re-prepared pages
  included. A page whose ledger records an older guidance digest while its receipt was
  reviewed under the current guidance keeps its receipt.
- **Documents that space rather than indent their paragraphs** (reports, papers) prepared
  before `0.6.0-dev.8` had each page merged into one unit. Re-prepare them entirely and review
  every page again. The new units have new IDs, and translations of the merged units are not
  reused. Indented books change only where prose opens after real paragraph white space.
- **Crops.** Pages prepared by 0.6.0 carry per-region `original.pdf` files; re-preparation
  removes them from the directories it re-exports. Run `littrans source gc PROJECT --dry-run`,
  then `--apply`, to remove crop directories that earlier `--replace` runs orphaned.

What re-preparation reads differently, in outline (the changelog has each rule):

- **Since 0.6.2** (`0.7.2-dev.1`, `0.7.2-dev.2`): the rows of a display split around its own
  label line are one asset, and a label line set just above or below its display binds to it.
  The panels of a composite figure under one caption, with no text inside their cluster, are
  one asset with a fragment per panel. The page-edge continuation flags skip a figure, table
  or caption at the page top or bottom.
- **Before 0.6.2**, in addition:
  - *Inline notation:* word spaces at asset edges follow the printed ink; operator names
    (`log`, `lim`, `Cov(`), exponents of text-face numbers (`2^{19937}`), closing brackets on
    the next line, accents over math letters and cases blocks in running text stay in one
    asset. A closing `!` is a factorial unless the sentence ends there.
  - *Displays:* printed equation labels leave the crop and bind as `equation_number`; one
    detector box over two labelled displays becomes two units; numerators, case rows and
    connectives that hold words stay in the display as declared conditions.
  - *Structure:* list labels, list continuations, paragraph white space, statement
    continuations, proof tombstones and page-top continuation flags follow the page's
    typography. Detector heading and caption labels no longer decide a kind on their own.
    Table boxes grow over their header rows.

### 8. Re-render

Re-render editions (`render`) and source checkpoints (`source render`) after re-preparing
pages. A project last rendered before `0.6.1-dev.6` also picks up relative links,
page-spanning paragraph merging in Markdown and HTML, and translated language in equation
lines. Rendering changes no record.

## After an interruption

Ask `workflow status` about the frozen batch IDs, recover the successful responses you saved,
and import them; imports are idempotent. Then dispatch only the missing work. A pending LaTeX
candidate does not reset a completed translation. A changed source unit or semantic
dependency does require a current review.
