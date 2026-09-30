# CLI reference

Commands, parameters and file contracts for direct CLI users and agents. For installation,
see [installation](installation.md); for working methods, see [source processing](source-processing.md)
and [translation workflow](translation-workflow.md).

## Contents

- [Invocation and common conventions](#invocation-and-common-conventions)
- [Runtime commands](#runtime-commands)
- [Project commands](#project-commands)
- [Source commands](#source-commands)
- [Asset commands](#asset-commands)
- [Context commands](#context-commands)
- [Translation commands](#translation-commands)
- [Workflow commands](#workflow-commands)
- [Task commands](#task-commands)
- [Data contracts](#data-contracts)
- [Compatibility aliases](#compatibility-aliases)

## Invocation and common conventions

Use `littrans` in the installed package environment, or the bundled launcher:

```text
python <plugin-root>/scripts/littrans.py --help
python <plugin-root>/scripts/littrans.py source extract --help
```

Replace `littrans` in examples with that launcher when needed. Uppercase arguments are placeholders.
Quote paths/text containing spaces. PROJECT is the project root, not necessarily the repository.
CLI paths resolve from the working directory; stored paths resolve from their documented owning root.

Page specs use 1-based PDF pages, not printed page numbers: `1`, `1-10`, `1,3,5-7`, or `all`
where accepted. Descending ranges and pages outside the PDF bounds are refused. Pages normalize to ordered unique values. ID lists use commas; supply IDs without
surrounding whitespace (the task wrapper does not trim them). Batch IDs match
`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`; task IDs match `task-[a-f0-9]{24}`.
Use emitted IDs and fingerprints; examples with uppercase IDs require replacement.

Tables list CLI defaults as JSON: `null` means unspecified and `required` means no default.
Command text explains runtime fallbacks. Choices lists only framework-enforced enumerations;
`[]` means no framework enumeration, not that domain validation accepts every value. Boolean pairs list both spellings separated by `/`.
`[OPTIONS]` means options from the command's table. `--help` works at every group and command;
it prints help and exits without running the operation. Root completion options are below.

Normal stdout is UTF-8 JSON with LF endings. The `--jsonl` queries emit one object per line.
JSONL inputs also use one object per line; an empty audit issue file means no findings.
Warnings, advisories and deprecation messages go to stderr. Help/completion output is text.
Keep stdout separate from stderr. Models reject unknown fields unless a contract allows them.

Exit 0 means the operation returned, not that diagnostics or quality gates passed. Read `passed`,
`ok`, issues and gate fields. Guarded domain or file errors exit 1; CLI usage errors exit 2.
`project tracked` explicitly exits 1 on tracking problems. Other runtime failures can exit nonzero.

Launcher/runtime environment overrides include `LITTRANS_CACHE_DIR`, `LITTRANS_LAYOUT_BASE_PYTHON`,
`LITTRANS_LAYOUT_PYTHON`, `LITTRANS_LAYOUT_MODEL` and generated-launcher `LITTRANS_PLUGIN_ROOT`.
See [environment variables](#environment-variables) for path semantics. Provider authentication belongs to the external CLI,
not translation submission files.

### Environment variables

| Variable | Effect |
| --- | --- |
| `LITTRANS_LAYOUT_PYTHON`, `LITTRANS_LAYOUT_MODEL` | Use an externally managed detector interpreter and weight directory instead of the cache. |
| `LITTRANS_LAYOUT_BASE_PYTHON` | The 3.10–3.13 interpreter `layout install` builds the detector environment from. |
| `LITTRANS_PLUGIN_ROOT` | The plugin directory a project's `tools/lt.py` launcher runs (one containing `scripts/littrans.py`); otherwise the launcher tries the root recorded when it was generated (or the same path under this user's home) while it exists, the install of the client running the session (detected from the same environment signals as workflow coordination: Claude Code's `~/.claude/plugins/installed_plugins.json` record, otherwise the highest version in that client's cache), the newest sibling of a recorded root outside every cache, and then every client's install by version (`~/.claude/plugins/cache/littrans/literature-translation/<version>`, `~/.codex/plugins/cache/…`, `~/.cursor/plugins/local/literature-translation`, `~/.qoder-cn/plugins/literature-translation`; build metadata such as `+codex.<stamp>` sorts as a later build of the same version). |
| `LITTRANS_LAUNCHER_VERBOSE` | `tools/lt.py` prints the plugin root it resolved. |
| `LITTRANS_CACHE_DIR` | Where the CLI and layout environments live, on every platform. Keep it outside `AppData` on Windows. |
| `XDG_CACHE_HOME` | The cache base on Linux and macOS when `LITTRANS_CACHE_DIR` is unset (Windows uses `%USERPROFILE%\.littrans`). |

### Root options

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `--install-completion` | `boolean` | no | `null` | `[]` | Install completion for current shell. |
| `--show-completion` | `boolean` | no | `null` | `[]` | Print completion for current shell. |

## Runtime commands

### littrans doctor

Check interpreter, dependencies and layout runtime.

```text
littrans doctor
```

No command-specific parameters.

Returns python, python_ok, build, modules, pdftoppm, pdfinfo and layout_runtime. Runs diagnostic subprocesses without installing or repairing. Inspect booleans even on exit 0.

Example:

```text
littrans doctor
```

### littrans status

Summarize a project.

```text
littrans status PROJECT
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |

Returns project identity, status, source/unit/translation counts, translation_status_counts, machine_reviewed_coverage, batches, review_issue_count and output_files. Reads saved records; grants no approval.

Example:

```text
littrans status PROJECT
```

### littrans layout status

Inspect the layout runtime.

```text
littrans layout status
```

No command-specific parameters.

Returns runtime status including ok and diagnostics. Runs interpreter/import probes; does not install anything.

Example:

```text
littrans layout status
```

### littrans layout install

Install or repair the isolated layout detector.

```text
littrans layout install [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `--python` | `path` | no | `null` | `[]` | Base interpreter for the layout environment. |
| `--force/--no-force` | `boolean` | no | `false` | `[]` | Force the operation described above; not an approval bypass. |
| `--model-source` | `str` | no | `"huggingface"` | `[]` | Weight download source. |
| `--repair/--no-repair` | `boolean` | no | `false` | `[]` | Repair environment, retaining verified weights. |

Writes the cache environment and downloads weights. `--repair` recreates the environment retaining verified weights; `--force` recreates it and redownloads weights. These modes are exclusive. `--model-source` accepts huggingface or modelscope; `--python` must be a compatible Python 3.10–3.13 base interpreter. Returns runtime status. See [runtime](runtime.md).

Example:

```text
littrans layout install --repair
```

## Project commands

### littrans project init

Initialize a project from a PDF.

```text
littrans project init SOURCE PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `SOURCE` | `path` | yes | `required` | `[]` | Command parameter; see command description. |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory. |
| `--preset/--profile` | `str` | no | `"technical-book"` | `[]` | Command parameter; see command description. |
| `--title` | `str` | no | `null` | `[]` | Command parameter; see command description. |
| `--source-language` | `str` | no | `"en"` | `[]` | Command parameter; see command description. |
| `--target-language` | `str` | no | `"zh-CN"` | `[]` | Command parameter; see command description. |
| `--repo-root` | `path` | no | `null` | `[]` | Where the handbook, records, ledger and launcher go when the project is nested in a larger repository (default: PROJECT). |

Writes project.yaml, context, glossary and record scaffold; records the PDF path without copying it. Returns [ProjectConfig](#model-projectconfig) plus scaffold. Title defaults from the source filename; `--repo-root` must contain PROJECT. Refuses a destination that already contains project.yaml; use scaffold for missing record files and the migration guide for existing projects.

Example:

```text
littrans project init SOURCE.pdf PROJECT
```

### littrans project models

Resolve host and role dispatch policy.

```text
littrans project models PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--host` | `str` | no | `"auto"` | `[]` | Coordination host; command text lists supported values. |

Returns host, per-role model policy and advisories. Reads configuration only. Host values: auto, codex, claude, opencode, cursor, qoder, generic. Unset policies preserve host defaults; dispatch values are not verified served-model identities.

Example:

```text
littrans project models PROJECT --host opencode
```

### littrans project scaffold

Create missing project record files.

```text
littrans project scaffold PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--repo-root` | `path` | no | `null` | `[]` | Containing repository for project records. |
| `--refresh/--no-refresh` | `boolean` | no | `false` | `[]` | Refresh plugin-owned project documentation. |

Writes missing handbook, records and launcher. `--refresh` regenerates plugin-owned docs/LITTRANS.md; user-owned files are preserved. `--repo-root` persists a containing record root; otherwise uses the saved root, detected ancestor record, or PROJECT. Returns scaffold accounting.

Example:

```text
littrans project scaffold PROJECT --refresh
```

### littrans project tracked

Check Git tracking of project records.

```text
littrans project tracked PROJECT
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |

Read-only. Returns tracking report with problems. Exits 1 for tracking gaps or incorrectly tracked private/generated material.

Example:

```text
littrans project tracked PROJECT
```

### littrans project rebuild

Rebuild a project in a new workspace, keeping v7 policy by default.

```text
littrans project rebuild OLD NEW [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `OLD` | `path` | yes | `required` | `[]` | Existing project to preserve. |
| `NEW` | `path` | yes | `required` | `[]` | New project directory. |
| `--settings` | `str` | no | `null` | `[]` | `preserve` (default for v7) or `preset` (required for older formats). |
| `--preset` | `str` | no | `null` | `[]` | Preset for `--settings preset`; defaults to the old project's preset. |

Preserves OLD; NEW must be a new destination. Copies source and reusable context/glossary/docs, not
translations, batches, QA, reviews, evidence, workflow progress or approvals. A v7 OLD keeps its
validated `settings.yaml` byte for byte and migrates `settings.local.yaml` executable bindings for
reviewers the new settings define (relative paths become absolute; bare command names stay PATH
lookups). `--settings preset` explicitly starts from a preset while keeping `document`. Older formats
can only start from a preset. The local `source_path` is replaced by the copied source; generated
native agent files and legacy manifest policy are not migrated.

Returns [ProjectConfig](#model-projectconfig) plus `rebuild`: `copied`, `unvalidated`,
`inherited_approvals` and `configuration` with `mode`, `source_schema_version`, `preset`,
`preserved` (top-level sections), `reset` (`path`, `before`, `after`, `operation` for each changed
field), `local_migrated` (`path`, `binding` of `path` or `command-name`), `not_migrated` (`path`,
`reason`) and `next_actions`. `unvalidated` lists the copied `context/` and `glossary/` files that
LitTrans neither validates nor maintains (project-defined records such as an extraction manifest);
they are copied unchanged and `next_actions` asks to review, move or delete them. The same
`unvalidated` and `configuration` are recorded in `derived/rebuild-provenance.json`, without
machine paths. NEW is staged beside it and inherits its parent directory's permissions.

Example:

```text
littrans project rebuild OLD NEW
```

### littrans project agents

Check or generate native project agents.

```text
littrans project agents PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--host` | `str` | no | `"codex"` | `[]` | Coordination host; command text lists supported values. |
| `--workspace` | `path` | no | `null` | `[]` | Containing workspace for native agents. |
| `--write/--no-write` | `boolean` | no | `false` | `[]` | Write generated resources. |
| `--check/--no-check` | `boolean` | no | `false` | `[]` | Explicitly request a check. |

Supports codex and opencode. Default checks only; `--write` and `--check` are exclusive. `--workspace` selects the containing repository. Returns host, workspace, changed, conflicts, written, note and OpenCode models. Writes only with `--write`; edited resources are preserved by refusing conflicts.

Example:

```text
littrans project agents PROJECT --host opencode --check
```

## Source commands

### littrans source inspect

Inspect source PDF pages.

```text
littrans source inspect PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--pages` | `str` | no | `"all"` | `[]` | PDF page selection. |

Writes source inspection evidence and returns page text/image diagnostics. Does not establish reviewed extraction.

Example:

```text
littrans source inspect PROJECT --pages 1-10
```

### littrans source verify

Validate source coverage and review evidence.

```text
littrans source verify PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--pages` | `str` | no | `"all"` | `[]` | PDF page selection. |
| `--force/--no-force` | `boolean` | no | `false` | `[]` | Force the operation described above; not an approval bypass. |

Returns passed, errors and verification evidence including receipt_packets. Writes verification reports as applicable. `--force` requests revalidation where supported, never bypasses review. A false passed value can still exit 0.

Example:

```text
littrans source verify PROJECT --pages 1-10
```

### littrans source probe

Build or extend the document structure profile.

```text
littrans source probe PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--pages` | `str` | no | `"all"` | `[]` | PDF page selection. |

Writes context/source-structure.json and returns profile metadata. Preserves existing rules; see [structure profile](#structure-profile).

Example:

```text
littrans source probe PROJECT --pages 1-10
```

### littrans source rescope

Move appended base-rule text into page-scoped rules.

```text
littrans source rescope PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--packet` | `str` | yes | `required` | `[]` | Saved source packet identifier. |
| `--pages` | `str` | yes | `required` | `[]` | PDF page selection. |
| `--label` | `str` | no | `""` | `[]` | Page-rule block label. |
| `--dry-run` | `boolean` | no | `false` | `[]` | Preview; see command side effects. |

`--packet` names a saved source packet; `--pages` supplies the new scope. The old base text must support the prefix split. Writes the profile unless `--dry-run`; returns proposed/applied changes. Does not rewrite historical receipts.

Example:

```text
littrans source rescope PROJECT --packet source-ID --pages 11-20 --dry-run
```

### littrans source gc

Find or remove orphan original-asset directories.

```text
littrans source gc PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--apply/--no-apply` | `boolean` | no | `false` | `[]` | Apply the operation. |
| `--dry-run/--no-dry-run` | `boolean` | no | `false` | `[]` | Preview; see command side effects. |

Choose exactly one of `--dry-run` or `--apply`. Returns directory accounting and live_source_packets/unreferenced_source_packets. Apply removes orphan asset directories only; neither mode removes source packets.

Example:

```text
littrans source gc PROJECT --dry-run
```

### littrans source render

Render a preserved-source checkpoint.

```text
littrans source render PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--pages` | `str` | no | `"all"` | `[]` | PDF page selection. |
| `--name` | `str` | no | `null` | `[]` | Output name; see command-specific fallback. |
| `--standalone` | `boolean` | no | `false` | `[]` | Embed source HTML images. |

Writes HTML under output/ and performs source verification. Default name: source-pNNNN-pNNNN. `--standalone` embeds images. Returns html, standalone, pages, units, kinds, assets, asset_export_methods, source_verified and attention.

Example:

```text
littrans source render PROJECT --pages 1-10 --standalone
```

### littrans source review-packets

Create source review inputs and a response template.

```text
littrans source review-packets PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--pages` | `str` | no | `"all"` | `[]` | PDF page selection. |
| `--host` | `str` | no | `"auto"` | `[]` | Coordination host; command text lists supported values. |

Writes or reuses packet and coverage artifacts. Returns packet_id, packet_path, packet_sha256, review_template, visual_report, pages and dispatch. See [source review](#source-review-contract).

Example:

```text
littrans source review-packets PROJECT --pages 1-10
```

### littrans source import-review

Import page decisions and corrections.

```text
littrans source import-review PROJECT INPUT_FILE [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `INPUT_FILE` | `path` | yes | `required` | `[]` | UTF-8 input file for this command. |
| `--confirm-visual-review` | `boolean` | no | `false` | `[]` | Attest actual visual inspection. |

Reads the [source review envelope](#source-review-contract); requires `--confirm-visual-review` after inspection. Transactionally writes corrections/receipts, invalidates dependencies and retires removed translations. Returns approved_pages, rejected_pages, changed_pages, deferred_pages, invalidated_pages, requires_new_packet and pruned_asset_directories.

Example:

```text
littrans source import-review PROJECT REVIEW.json --confirm-visual-review
```

### littrans source extract

Preserve native prose and original assets.

```text
littrans source extract PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--pages` | `str` | no | `"all"` | `[]` | PDF page selection. |
| `--replace/--no-replace` | `boolean` | no | `false` | `[]` | Reprepare cached pages. |
| `--discard-overrides` | `boolean` | no | `false` | `[]` | Drop recorded corrections during replacement. |
| `--allow-missing-layout` | `boolean` | no | `false` | `[]` | Explicitly allow missing layout detector. |
| `--redetect` | `boolean` | no | `false` | `[]` | Rerun detector during replacement. |

Writes units, asset registry, page evidence and review packets. Cached pages are reused unless `--replace`. Replacement replays recorded overrides/layout; `--redetect` requires `--replace`, and `--discard-overrides` explicitly drops corrections during replacement. `--allow-missing-layout` requires explicit user authorization. Returns [source results](#source-results).

Example:

```text
littrans source extract PROJECT --pages 1-10
```

## Asset commands

### littrans assets submit

Submit structured candidates.

```text
littrans assets submit PROJECT INPUT_FILE
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `INPUT_FILE` | `path` | yes | `required` | `[]` | UTF-8 input file for this command. |

Reads [AssetSubmission](#model-assetsubmission) JSON. Validates scope, dispatch echoes, image hashes and kind/format. Writes candidates, receipt and index; returns submission accounting. Identical receipt replay reports replayed=true without replacing newer valid evidence.

Example:

```text
littrans assets submit PROJECT CANDIDATES.json
```

### littrans assets packet

Create transcription or asset-review inputs.

```text
littrans assets packet PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--asset-ids` | `str` | yes | `required` | `[]` | Comma-separated stable asset IDs. |
| `--stage` | `str` | no | `"transcribe"` | `[]` | Domain stage; see constraints above. |
| `--revision-notes` | `str` | no | `null` | `[]` | Correction request bound to previous asset evidence. |
| `--host` | `str` | no | `"auto"` | `[]` | Coordination host; command text lists supported values. |

Stages: transcribe, asset-audit. IDs must exist. Writes packet inputs and audit comparison artifacts; revision notes bind a correction request to prior evidence. Returns packet/dispatch metadata. See [asset contract](#asset-contract).

Example:

```text
littrans assets packet PROJECT --asset-ids ASSET_ID --stage transcribe
```

### littrans assets import-review

Import independent asset decisions.

```text
littrans assets import-review PROJECT INPUT_FILE [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `INPUT_FILE` | `path` | yes | `required` | `[]` | UTF-8 input file for this command. |
| `--confirm-visual-review` | `boolean` | no | `false` | `[]` | Attest actual visual inspection. |

Reads [AssetReviewSubmission](#model-assetreviewsubmission). Requires `--confirm-visual-review`, a distinct reviewer and current candidate/image/render hashes. Writes review evidence/index; returns review_sha256, reviewed and replayed.

Example:

```text
littrans assets import-review PROJECT DECISIONS.json --confirm-visual-review
```

### littrans assets status

Report current representation states.

```text
littrans assets status PROJECT
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |

Returns assets keyed by ID and counts keyed by state. Revalidates evidence; corrupt or stale records cannot confer verified status.

Example:

```text
littrans assets status PROJECT
```

## Context commands

### littrans context check

Check context and terminology.

```text
littrans context check PROJECT
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |

Read-only. Returns snapshot, missing paths and glossary diagnostics. See [context contract](#context-and-glossary).

Example:

```text
littrans context check PROJECT
```

### littrans context snapshot

Save context fingerprints.

```text
littrans context snapshot PROJECT DESTINATION
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `DESTINATION` | `path` | yes | `required` | `[]` | New snapshot path inside project. |

DESTINATION resolves from the working directory, must stay inside PROJECT and must not exist. Writes JSON and returns path plus snapshot fields.

Example:

```text
littrans context snapshot PROJECT PROJECT/context-before.json
```

### littrans context impact

Compare saved and current context.

```text
littrans context impact PROJECT PREVIOUS
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `PREVIOUS` | `path` | yes | `required` | `[]` | Saved context snapshot path. |

PREVIOUS must be a version-1 snapshot of the same source with the exact known file set. Read-only; returns changed paths, effects and note. It does not replace domain verification.

Example:

```text
littrans context impact PROJECT PROJECT/context-before.json
```

### littrans context glossary lookup

Query relevant terminology.

```text
littrans context glossary lookup PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--batch-id` | `str` | no | `null` | `[]` | Batch identifier. |
| `--pages` | `str` | no | `null` | `[]` | PDF page selection. |
| `--unit-ids` | `str` | no | `null` | `[]` | Comma-separated source unit IDs. |
| `--text` | `path` | no | `null` | `[]` | UTF-8 text file for unscoped term matching. |
| `--kind` | `str` | no | `null` | `[]` | Reference glossary kind filter. |
| `--jsonl/--no-jsonl` | `boolean` | no | `false` | `[]` | Stream one JSON object per line. |

Choose exactly one of `--batch-id`, `--pages`, `--unit-ids` or `--text`. `--kind` filters references only; text lookup ignores scope. Returns selection, approved, reference groups and totals. `--jsonl` emits entries with channel and reference kind. Read-only.

Example:

```text
littrans context glossary lookup PROJECT --pages 1-10
```

### littrans context glossary check

Validate glossary files and find unmatched entries.

```text
littrans context glossary check PROJECT
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |

Read-only; returns counts and unmatched-entry diagnostics. Malformed files refuse loading; unmatched entries do not automatically produce a nonzero exit.

Example:

```text
littrans context glossary check PROJECT
```

## Translation commands

### littrans translation submit

Import batch translations.

```text
littrans translation submit PROJECT BATCH_ID INPUT_FILE
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `BATCH_ID` | `str` | yes | `required` | `[]` | Batch identifier. |
| `INPUT_FILE` | `path` | yes | `required` | `[]` | UTF-8 input file for this command. |

Reads [TranslationRecord](#model-translationrecord) JSONL, validates editable scope and source/image bindings, writes current records/history and invalidates changed evidence. Returns stored record array. See [translation contract](#translation-contract).

Example:

```text
littrans translation submit PROJECT BATCH_ID TRANSLATION.jsonl
```

### littrans translation qa

Run deterministic quality checks.

```text
littrans translation qa PROJECT BATCH_ID
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `BATCH_ID` | `str` | yes | `required` | `[]` | Batch identifier. |

Writes and returns [QAReport](#model-qareport), bound to translation and QA-context fingerprints. Checks batch and dependency closure. Read passed/errors; failed quality checks do not necessarily cause process failure.

Example:

```text
littrans translation qa PROJECT BATCH_ID
```

### littrans translation approve

Set approval after prerequisites pass.

```text
littrans translation approve PROJECT BATCH_ID [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `BATCH_ID` | `str` | yes | `required` | `[]` | Batch identifier. |
| `--level` | `str` | no | `"machine"` | `[]` | Requested approval level. |
| `--confirm-user-approved/--no-confirm-user-approved` | `boolean` | no | `false` | `[]` | Attest actual user approval. |

Levels: machine, external, human. All require verified source, current passing QA, complete three-lens audits and no open blocker/major issues. External additionally requires external_approvable; human requires `--confirm-user-approved` backed by actual user approval. Writes statuses and returns a JSON object with status; never creates missing review evidence.

Example:

```text
littrans translation approve PROJECT BATCH_ID --level machine
```

### littrans translation render

Export a scoped reading edition.

```text
littrans translation render PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--pages` | `str` | no | `null` | `[]` | PDF page selection. |
| `--batch-id` | `str` | no | `null` | `[]` | Batch identifier. |
| `--batch-ids` | `str` | no | `null` | `[]` | Comma-separated batch identifiers in source order. |
| `--name` | `str` | no | `null` | `[]` | Output name; see command-specific fallback. |
| `--allow-draft/--no-allow-draft` | `boolean` | no | `false` | `[]` | Permit a draft reading edition. |
| `--originals-only/--no-originals-only` | `boolean` | no | `false` | `[]` | Force original images. |

Choose exactly one of `--pages`, `--batch-id` or `--batch-ids`. `--name` is required except for a single batch. Batch sets must be ordered, nonoverlapping and consecutive within series. Writes Markdown, bilingual HTML and reports. `--allow-draft` permits draft output; `--originals-only` forces original images. Returns [render results](#render-results).

Example:

```text
littrans translation render PROJECT --batch-id BATCH_ID
```

### littrans translation batch create

Create batches from verified source.

```text
littrans translation batch create PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--pages` | `str` | yes | `required` | `[]` | PDF page selection. |
| `--max-words` | `int` | no | `null` | `[]` | Batch word target, default from profile. |
| `--prefix` | `str` | no | `null` | `[]` | Batch identifier prefix. |
| `--untranslated-only/--no-untranslated-only` | `boolean` | no | `false` | `[]` | Select untranslated units with group context. |
| `--unit-ids` | `str` | no | `null` | `[]` | Comma-separated source unit IDs. |

`--max-words` defaults from profile and must be at least 100. Explicit unit IDs and untranslated-only selections freeze scope and must preserve complete logical groups/continuations. Writes manifests and batch inputs; returns [BatchManifest](#model-batchmanifest) array.

Example:

```text
littrans translation batch create PROJECT --pages 1-10
```

### littrans translation batch show

Read a batch.

```text
littrans translation batch show PROJECT BATCH_ID
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `BATCH_ID` | `str` | yes | `required` | `[]` | Batch identifier. |

Returns [BatchManifest](#model-batchmanifest) plus translation_file and translation_exists. No writes.

Example:

```text
littrans translation batch show PROJECT BATCH_ID
```

### littrans translation batch refresh

Refresh batch inputs.

```text
littrans translation batch refresh PROJECT BATCH_ID
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `BATCH_ID` | `str` | yes | `required` | `[]` | Batch identifier. |

Writes refreshed manifest, source/context inputs and inputs. Frozen scope retains selected IDs; removed units or incomplete groups require a new selection. Returns [BatchManifest](#model-batchmanifest).

Example:

```text
littrans translation batch refresh PROJECT BATCH_ID
```

### littrans translation review import

Import one batch's audit issues.

```text
littrans translation review import PROJECT BATCH_ID INPUT_FILE [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `BATCH_ID` | `str` | yes | `required` | `[]` | Batch identifier. |
| `INPUT_FILE` | `path` | yes | `required` | `[]` | UTF-8 input file for this command. |
| `--lenses` | `str` | no | `"fidelity,technical,chinese-style"` | `[]` | Comma-separated audit lenses. |

Reads [ReviewIssue](#model-reviewissue) JSONL; empty file means no findings. `--lenses` selects comma-separated fidelity,technical,chinese-style. Writes issues and audit evidence; returns issue array. Coordinated audits should use packet-bound import-set.

Example:

```text
littrans translation review import PROJECT BATCH_ID ISSUES.jsonl --lenses fidelity
```

### littrans translation review import-set

Import packet-bound audit results.

```text
littrans translation review import-set PROJECT PACKET_MANIFEST ISSUES_JSONL
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `PACKET_MANIFEST` | `path` | yes | `required` | `[]` | Saved audit manifest path. |
| `ISSUES_JSONL` | `path` | yes | `required` | `[]` | Audit JSONL; empty means no findings. |

Reads saved audit manifest and issue JSONL (possibly empty). Validates bindings, routes context issues to owning batches and canonicalizes IDs. Writes issues/audit evidence; returns packet_id, lens, per-batch imported counts and id_map.

Example:

```text
littrans translation review import-set PROJECT MANIFEST.json ISSUES.jsonl
```

### littrans translation review resolve

Record issue dispositions.

```text
littrans translation review resolve PROJECT BATCH_ID ISSUE_ID [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `BATCH_ID` | `str` | yes | `required` | `[]` | Batch identifier. |
| `ISSUE_ID` | `str` | yes | `required` | `[]` | One issue ID or comma-separated IDs. |
| `--status` | `choice` | no | `"resolved"` | `["open", "resolved", "rejected", "waived"]` | Issue disposition. |
| `--resolution` | `str` | yes | `required` | `[]` | Evidence explaining the disposition. |

ISSUE_ID accepts comma-separated canonical or reviewer IDs. `--status open` is rejected by the domain validator despite appearing in the shared enum. Resolution must explain the disposition; closure records a timestamp. Ambiguous reviewer IDs require the canonical ID. Writes issues; returns one object for one issue, an array otherwise.

Example:

```text
littrans translation review resolve PROJECT BATCH_ID ISSUE_ID --resolution "Corrected the condition."
```

### littrans translation review issues

List review issues.

```text
littrans translation review issues PROJECT BATCH_ID [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `BATCH_ID` | `str` | yes | `required` | `[]` | Batch identifier. |
| `--all` | `boolean` | no | `false` | `[]` | Include closed issues. |
| `--jsonl/--no-jsonl` | `boolean` | no | `false` | `[]` | Stream one JSON object per line. |

Open issues by default; `--all` includes closed issues. Read-only. Returns [ReviewIssue](#model-reviewissue) array, or JSONL omitting null fields.

Example:

```text
littrans translation review issues PROJECT BATCH_ID --jsonl
```

### littrans translation review status

Check audit coverage.

```text
littrans translation review status PROJECT BATCH_ID
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `BATCH_ID` | `str` | yes | `required` | `[]` | Batch identifier. |

Read-only. Returns batch_id, audit_exists, audit_lenses_complete, audit_coverage, audit_stale_reasons, counts, open_blocking_issues and publishable. This publishable value does not substitute for QA or external gates.

Example:

```text
littrans translation review status PROJECT BATCH_ID
```

### littrans translation review external

Run external CLI review.

```text
littrans translation review external PROJECT BATCH_ID [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `BATCH_ID` | `str` | yes | `required` | `[]` | Batch identifier. |
| `--dry-run/--no-dry-run` | `boolean` | no | `false` | `[]` | Preview; see command side effects. |

Requires configuration and applicable internal gates. Writes isolated packet, raw/normalized evidence, attempts and review records. `--dry-run` prepares local artifacts/commands but makes no provider call. Invocation failures advance ordered fallbacks; content findings do not. Returns run/status or dry-run metadata.

Example:

```text
littrans translation review external PROJECT BATCH_ID --dry-run
```

### littrans translation review external-migrate

Retained entry for older callers; no in-place migration is supported in this branch.

```text
littrans translation review external-migrate PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--apply/--no-apply` | `boolean` | no | `false` | `[]` | Apply the operation. |

Unsupported project formats fail with a rebuild instruction, without writes. For v7 projects,
returns changed=false and applied=false with config CLI next actions; `--apply` cannot rewrite
the manifest. Manage reviewer definitions through `config apply`.

Example:

```text
littrans translation review external-migrate PROJECT
```

### littrans translation review external-adjudicate

Record external/recheck comparison.

```text
littrans translation review external-adjudicate PROJECT BATCH_ID INPUT_FILE
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `BATCH_ID` | `str` | yes | `required` | `[]` | Batch identifier. |
| `INPUT_FILE` | `path` | yes | `required` | `[]` | UTF-8 input file for this command. |

Reads [adjudication JSON](#external-review-contract). Requires current run/recheck, exact issue coverage and evidence-based reasons. Writes decisions and issue dispositions; returns external gate status.

Example:

```text
littrans translation review external-adjudicate PROJECT BATCH_ID DECISION.json
```

### littrans translation review external-status

Read external gate state.

```text
littrans translation review external-status PROJECT BATCH_ID
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `BATCH_ID` | `str` | yes | `required` | `[]` | Batch identifier. |

Returns batch_id, translation_fingerprint, verdict, primary (run or null), recheck, open_substantive_issues and external_approvable. Validates saved evidence; no provider calls.

Example:

```text
littrans translation review external-status PROJECT BATCH_ID
```

## Workflow commands

### littrans workflow next

Select the next work wave.

```text
littrans workflow next PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--limit` | `int` | no | `null` | `[]` | Wave batch limit; host-specific fallback. |
| `--start-at` | `str` | no | `null` | `[]` | Inclusive starting batch ID. |
| `--through` | `str` | no | `null` | `[]` | Inclusive ending batch ID. |
| `--host` | `str` | no | `"auto"` | `[]` | Coordination host; command text lists supported values. |

Returns stage, batch_ids, requested_batch_ids, ready_tasks, optional_asset_tasks, audit_stale, host, limit and unbatched-page diagnostics. May prepare packets; does not run models. Bounds are inclusive ordered batch IDs of the same series. See [workflow contract](#workflow-contract) for limits.

Example:

```text
littrans workflow next PROJECT --host codex
```

### littrans workflow status

Report selected batches' readiness.

```text
littrans workflow status PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--batch-ids` | `str` | yes | `required` | `[]` | Comma-separated batch identifiers in source order. |
| `--host` | `str` | no | `"auto"` | `[]` | Coordination host; command text lists supported values. |

Returns aggregate stage (or mixed), stages, lanes, ready tasks, stale-audit details and advisories. May prepare packets while resolving readiness. Coverage gaps inside the requested range refuse coordination.

Example:

```text
littrans workflow status PROJECT --batch-ids BATCH_ID
```

### littrans workflow packet

Create domain inputs for batches.

```text
littrans workflow packet PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--stage` | `str` | yes | `required` | `[]` | Domain stage; see constraints above. |
| `--batch-ids` | `str` | yes | `required` | `[]` | Comma-separated batch identifiers in source order. |
| `--lens` | `str` | no | `null` | `[]` | One audit lens: fidelity, technical or chinese-style. |
| `--host` | `str` | no | `"auto"` | `[]` | Coordination host; command text lists supported values. |

Stages: source-review, translate, revise, audit, transcribe, asset-audit. Audit requires one lens; translate/revise require one batch and no lens. Writes inputs. Returns [WorkflowPacketManifest](#model-workflowpacketmanifest), source material, asset packet(s), or a no-pending-work object.

Example:

```text
littrans workflow packet PROJECT --stage audit --batch-ids BATCH_ID --lens fidelity
```

### littrans workflow prune-packets

Find or remove regenerable legacy packet files.

```text
littrans workflow prune-packets PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--batch-ids` | `str` | no | `null` | `[]` | Comma-separated batch identifiers in source order. |
| `--apply/--no-apply` | `boolean` | no | `false` | `[]` | Apply the operation. |
| `--dry-run/--no-dry-run` | `boolean` | no | `false` | `[]` | Preview; see command side effects. |

Choose exactly one of `--apply` or `--dry-run`; optional batch IDs restrict scope. Returns mode, candidates, candidate_bytes and removed. Removes only supported legacy files, not arbitrary evidence.

Example:

```text
littrans workflow prune-packets PROJECT --dry-run
```

### littrans workflow metrics

Summarize evidence and usage.

```text
littrans workflow metrics PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--batch-ids` | `str` | no | `null` | `[]` | Comma-separated batch identifiers in source order. |

Read-only, optionally scoped by batches. Returns history/no-op counts and ratio, packet bytes, page receipts, audit counts, external runs/attempts, token/provider-turn totals, duration and reported cost. Missing usage is not inferred consumption.

Example:

```text
littrans workflow metrics PROJECT
```

## Task commands

### littrans task create

Create a durable task and handoff.

```text
littrans task create PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--stage` | `str` | yes | `required` | `[]` | Domain stage; see constraints above. |
| `--batch-ids` | `str` | no | `null` | `[]` | Comma-separated batch identifiers in source order. |
| `--pages` | `str` | no | `null` | `[]` | PDF page selection. |
| `--asset-ids` | `str` | no | `null` | `[]` | Comma-separated stable asset IDs. |
| `--lens` | `str` | no | `null` | `[]` | One audit lens: fidelity, technical or chinese-style. |
| `--host` | `str` | no | `"auto"` | `[]` | Coordination host; command text lists supported values. |
| `--objective` | `str` | no | `null` | `[]` | Bound scout/terminology objective. |
| `--revision-notes` | `str` | no | `null` | `[]` | Correction request bound to previous asset evidence. |

Choose exactly one selector: batch IDs, pages or asset IDs. See [task contract](#task-contract) for stage requirements. Writes immutable inputs, instruction snapshot and pending state; returns task status/handoff. Does not dispatch a model.

Example:

```text
littrans task create PROJECT --stage translate --batch-ids BATCH_ID
```

### littrans task status

Read task state.

```text
littrans task status PROJECT [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `--task-id` | `str` | no | `null` | `[]` | Task identifier returned by create. |

`--task-id` returns one status; otherwise returns an object with tasks array. result_available is separate from import and approval. No writes.

Example:

```text
littrans task status PROJECT
```

### littrans task claim

Claim an unfinished task.

```text
littrans task claim PROJECT TASK_ID [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `TASK_ID` | `str` | yes | `required` | `[]` | Task identifier returned by create. |
| `--executor` | `str` | yes | `required` | `[]` | Actual executor identity. |
| `--mode` | `str` | no | `"fresh-session"` | `[]` | Execution mode declaration. |

Executor must be nonempty; mode is subagent or fresh-session. External-recheck requires subagent. Enforces writer conflicts and declared reviewer independence. Writes claimed state and returns task status.

Example:

```text
littrans task claim PROJECT TASK_ID --executor worker-1 --mode subagent
```

### littrans task release

Release a stopped executor's claim.

```text
littrans task release PROJECT TASK_ID [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `TASK_ID` | `str` | yes | `required` | `[]` | Task identifier returned by create. |
| `--executor` | `str` | yes | `required` | `[]` | Actual executor identity. |

Executor must match the current owner; imported tasks cannot be released. Writes pending state with no executor, returns task status. Confirm executor termination before calling.

Example:

```text
littrans task release PROJECT TASK_ID --executor worker-1
```

### littrans task receive

Receive a claimed task result.

```text
littrans task receive PROJECT TASK_ID [OPTIONS]
```

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory containing project.yaml. |
| `TASK_ID` | `str` | yes | `required` | `[]` | Task identifier returned by create. |
| `--result` | `path` | no | `null` | `[]` | Result file; defaults to task result.json. |
| `--confirm-visual-review/--no-confirm-visual-review` | `boolean` | no | `false` | `[]` | Attest actual visual inspection. |

Default input is task result.json; `--result` selects another file. Validates source/context/instruction/packet bindings, saves input and calls domain importer. Source/asset reviews need visual confirmation; translation also runs QA. Returns status with outcome. Identical imported bytes replay; different bytes need a new task.

Example:

```text
littrans task receive PROJECT TASK_ID --result RESULT.json
```

## Data contracts

Field tables below describe serialized public records and accepted inputs. Required means the
field must be present; nullable required fields still need an explicit null. Defaults are applied
by the model, while domain importers enforce the additional bindings described here. Output
records and packet templates contain computed IDs, hashes and timestamps; preserve these values.
Examples with placeholder fingerprints illustrate shape only and must be bound to a real packet.

### Contract index

- [Project configuration](#project-configuration)
- [Context and glossary](#context-and-glossary)
- [Structure profile](#structure-profile)
- [Source review and overrides](#source-review-contract)
- [Source command results](#source-results)
- [Translation and audit](#translation-contract)
- [Assets](#asset-contract)
- [Tasks](#task-contract)
- [Workflow](#workflow-contract)
- [External review](#external-review-contract)
- [Rendering](#render-results)
- [Record field tables](#record-field-tables)

### Project configuration

`project.yaml` is the immutable v7 source/project manifest. Shared policies are complete,
versioned `settings.yaml` objects; `settings.local.yaml` holds ignored source and executable
bindings. Runtime state is in `derived/project-state.json`. See [configuration](configuration.md)
and the generated [field reference](settings-fields.md) for the authoritative settings contract.
Older project versions are rejected. Source rebindings must match the manifest hash.

Presets expand at initialization and explicit reset; runtime never loads a profile file.
Use `config apply` for related changes and `config show --effective --host HOST` to inspect
inheritance, origins and unsupported overrides. `ProjectConfig` below is an internal resolved
runtime view, not the on-disk settings format.

### Context and glossary

`context/document-brief.md` and `context/style-guide.md` are UTF-8 Markdown. Their complete
content participates in translation audit dependencies. The structure profile and glossary have
more specific scope rules below.

A context snapshot has `schema_version: 1`, `source_sha256`, and `files`, mapping exactly these
paths to SHA-256 strings or null when absent: `context/document-brief.md`, `context/style-guide.md`,
`context/source-structure.json`, `glossary/approved.yaml`, `glossary/reference.yaml`,
`glossary/candidates.yaml`. Snapshot stdout also includes `path`; task envelopes embed the snapshot
without it. Impact comparison accepts extra top-level fields but requires the exact file set and
same source digest.

**Glossary files and matching**

Three glossary files share one entry schema — a `terms` list whose entries carry `source` and
optionally `aliases` (other attested source forms), `match`, `scope` (`document`, `page:N` or a
parent unit ID), `status`, `target`, `forbidden` and any project-defined key — and differ only
in effect:

| File | Effect | Reaches packets | In the audit hash |
| --- | --- | --- | --- |
| `approved.yaml`, `status` absent or `approved` | hard per-unit QA gate | entries matching the packet's units | those entries |
| `approved.yaml`, `status: reference-only` | binding, never gated | same filter | same |
| `reference.yaml` (`status` defaults to `reference-only`) | binding, never gated; grouped by `kind` | same filter | same |
| `status: proposed` in either file | inert | no | no |
| `candidates.yaml` | none: the record of promotion decisions | no | no |

- Reference entries are the channel for data that grows with the chapters but must not gate:
  proper names kept in source form, one-word-two-senses registers, chapter usage notes.
  `kind` (default `reference`, e.g. `proper-name`, `sense`) groups them in packets; every other
  key (`targets`, `rule`, `note`, `first_seen`, ...) is shown verbatim. `status: approved` inside
  `reference.yaml` is refused: that file never gates. Because reference entries are filtered per
  unit like approved terms, appending a chapter's names changes only the audit context of the
  batches that mention them, and correcting an entry resets only the batches it matches — the
  two context files, by contrast, are hashed whole.
- Packets show the gated entries under `# Relevant approved terminology` (`approved_terms`)
  and, only when at least one matches, the reference entries under
  `# Relevant reference terminology (not gated)` (`reference_terms`, one list per `kind`); the
  batch `context.md` and external-review packets carry the same two sections. A project without
  reference entries keeps the audit context it had before the channel existed.
- `candidates.yaml` entries without `status` (or `status: proposed`) are undecided and are
  listed in the finalize unresolved report; entries whose status records a decision
  (`reference-only`, `rejected`, ...) are only counted there.
- `context glossary lookup PROJECT --batch-id ID | --pages SPEC | --unit-ids IDS | --text FILE`
  lists the approved and reference entries a selection receives, with the packet's own scope
  and folding rules (`--kind` narrows the reference groups, `--jsonl` emits one entry per
  line); `context glossary check PROJECT` loads every file and reports entries matching no prepared
  unit. Both are read-only.
- The unit's source representations (text, Markdown, table cells, figure labels) minus quoted
  titles are folded before matching, and so is `source`. A quoted title is a double-quoted phrase
  of at least two words whose words are capitalised except `a an and as at but by for from in
  into nor of on or over the to via vs with` (`“Binding Theory”`); every quotation of a
  `bibliography` unit counts as one. A quoted term (`“strict mode”`) is matched. Folding covers
  precomposed, combining and TeX spacing accents (`Hölder` ≡ `H¨older`, `Lévy` ≡ `L´evy`), ligatures, curly quotes and apostrophes
  (`Chebyshev's` ≡ `Chebyshev’s`), dash variants, whitespace runs and case. QA and the
  `relevant_terms` packet injection share this folding, so a term shown to the translator is the
  term QA enforces.
- `match` selects how `source` is located in the folded text: `substring` (default; `measure`
  also hits `measurable`), `word` (no letter/digit on either side), or `regex` (a Python pattern
  searched case-insensitively in the folded text, e.g. `\bpartition\b(?! function)`). The
  literal characters of a regex are folded like a substring source (`Hölder`, `Chebyshev’s`
  and `H¨older`, `Chebyshev's` are the same pattern) while escape sequences such as `\b`, `\B`
  or `\s` are kept verbatim. Invalid modes or patterns fail loading.
- When `source` occurs in a unit, `target` must appear in that unit's translation
  (`approved-term-missing`). A `source` that matches no prepared unit at all is reported once per
  QA run as the warning `approved-term-never-matched`; fix the spelling or narrow the entry.
- `forbidden` wording is checked in **every** translated unit and asset companion, whether or
  not that unit contains `source`. List only wording that is wrong in every context (a wrong
  transliteration), never a rendering that is merely wrong for this term (`mean` → 意味着).
- Editing a gated entry changes the QA context of every batch and the audit context of batches
  whose relevant terms change (existing audits become `audit_stale`); finish the gate baseline
  before `source extract`, or at the latest before the audit wave. Drafts belong in
  `glossary/candidates.yaml`, which has no effect until an entry is moved into `approved.yaml`
  or `reference.yaml`.


### Structure profile

`context/source-structure.json` uses [StructureProfile](#model-structureprofile), containing
[PageRules](#model-pagerules). Page lists must be unique and in PDF bounds. A page-rule block
uses an explicit page spec inside profile scope, never `all`. Rule values are strings; categories
are open-ended. `inspected_pages` must be a subset of profile pages. A reviewed profile requires
nonempty inspected pages, base handling rules and review notes. A page's effective guidance is the base rule text followed by every matching
block, joined by newline for each category. Receipts bind that effective guidance, not labels,
formatting or observation updates. Changing base rules affects every page; extending scoped rules
affects only pages whose effective text changes. `source rescope` supports a prefix-preserving
split of appended base text using the profile embedded in an earlier source packet.

### Source review contract

Input is one JSON object from the emitted `review-template.json`, with `packet_id`, `packet_sha256`,
`reviewer` (nonempty identity), `visual_report_sha256`, and `pages` (page-decision objects). The packet ID,
page fingerprints and report hash must match current saved evidence. A correction is submitted
through an override inside a decision, never by editing derived records directly.

| Envelope field | Type | Requirement |
| --- | --- | --- |
| `packet_id` | string | Exact saved packet ID |
| `packet_sha256` | string | Exact packet-file digest from the template |
| `visual_report_sha256` | string | Report binding from the template |
| `reviewer` | string | Nonempty reviewer identity |
| `pages` | array of page decisions | Each page at most once, within the packet; partial packet review is accepted |

A page decision requires `page` (integer) and `fingerprint` (current packet page binding).
Approval flags and confirmation lists are defined below; omitted required attestations fail
approval. `issues` is an array (empty for approval), `notes` is explanatory text and `override`
is an optional page correction object. The template's `context` is generated inspection context,
not authority to change source records. Rejected pages appear as a page-to-failure-list object
in `rejected_pages`; changed/deferred/invalidated pages are separate integer arrays.

### Packet and receipt bindings

`source review-packets` writes `packets/source-<hash>/packet.json`, `review-template.json` and
`coverage.html`, and its output adds the `dispatch` (host, `source-review` role, model, effort) for
the `literature-source-reviewer` subagent that reviews, corrects and imports the pages. Coverage HTML and its referenced page images are bound by the packet
`visual_report` manifest; relative image URLs keep reports portable. A damaged report is rebuilt
under a new packet identity and cannot silently restore prior approval.

Source-review receipts bind the decision, reviewer, source, page fingerprint and original packet
identity/hash with `receipt_sha256`. Submissions must echo `visual_report_sha256` after
inspection, and receipts retain it. Approval consumers (`source verify`, batch creation,
workflow coordination) verify the receipt and packet, compare the structure guidance of the
page in the packet's embedded profile with the current profile (`fidelity-source-unverified`:
`source structure guidance changed since review for page N (handling_rules: …)`), then recheck
the visual decision conditions. Legacy receipts without these bindings require a fresh visual
source review import; no automatic approval migration is performed.

The packet directory a receipt names (`packets/source-<hash>/packet.json`, `coverage.html`
and the page images its manifest lists) is a live dependency of that review however many newer
packets exist: keep it on disk and under version control for as long as the receipt is meant to
verify. `source verify` lists the packets its verified receipts depend on in `receipt_packets`,
`source gc` reports `live_source_packets` and `unreferenced_source_packets` without deleting
either (a review file not yet imported may name an unreferenced one), and a receipt whose
packet is missing fails verification with a message naming the packet as a review dependency.

### Decision fields

Each entry of `pages` in the submitted review carries the packet page's `page` and `fingerprint`
plus the attestation flags from the template: `viewed_original`, `coverage_complete`,
`boundaries_complete`, `reading_order_correct`, `grouping_checked`, and, when the ledger requires
them, `layout_fallback_checked` (layout not `ok`), `overflow_canvas_checked` (a page canvas
override) and `formula_conditions_checked` (declared formula conditions: the reviewer confirms
the listed declarations are correct and complete, not that words are absent from the crop).
The template's `context` block lists what the ledger already knows for that page —
`formula_conditions` (asset, text, box), `grouping_pending` asset IDs, `boundary_diagnostics`,
`findings` and `structure_checks` — so the review confirms a list instead of guessing from crops.

`structure_checks` holds the page's semantic decisions, one row each (the coverage report prints
them per page). `roles`: every read unit prepared as a `heading` or `caption`, every unit that
opens with a statement, run-in or proof label, and every unit whose detector heading/caption
label preparation overruled (`detector_label`), with its `kind` and an excerpt. `joins`: every
native text block whose text was joined into another unit rather than becoming one, with that
unit and an excerpt. Page flags say nothing about which of these was looked at, so each is
confirmed on its own: `confirmed_roles: [{"unit_id", "kind"}]` names every listed role with the
kind read on the original — face and size against the page's running text, indent and vertical
space, the same form on neighbouring pages — and `confirmed_joins: [{"block"}]` every listed
join after checking that no paragraph break (an indent, white space, a new list item) separates
the block from the text before it. A page passes only when both lists cover their rows
(`role-unconfirmed`, `join-unconfirmed`), a confirmed kind equals the recorded one
(`role-disputed`: correct the page with a `units` override instead) and no confirmation names a
unit the list does not hold (`role-not-listed`); an entry in another shape is an error naming
the page. A packet made before `structure_checks` existed asks for neither list, so receipts
bound to it keep passing. List containers are flat: a lead-in paragraph is the parent of its
items and of the displays and explanation paragraphs inside them; to hang a display from the
item itself, use a `units` override.

The approval gate and the checkpoint's attention list share one predicate
(`page_review_findings`): an approved page is a page that needs no attention. A page passes
only when every required flag is `true`, `issues` is empty, no `override` is present and no
finding remains: `grouping-pending` (an asset with `grouping_pending` that the decision does
not list in `accepted_grouping_pending: [{"asset_id", "reason"}]` with a non-empty reason; an
entry in another shape, such as a bare asset ID string, is an error that names the page rather
than a silently ignored acceptance),
`undeclared-formula-language` (language inside a `math` crop that no formula condition
declares) and `recoverable-prose-in-image` (a `math`/`mixed-region` asset owning six or more
undeclared words). A receipt that does not pass records the reasons in `failures`, and the
import result lists them in `rejected_pages`. A `mixed-region` image is never textual coverage
of recoverable paragraphs: split the source region and obtain a fresh packet.

Overrides are applied before approvals. Decisions whose dependency fingerprints changed because
another decision in the same import corrected a page appear in `deferred_pages`; corrected pages
appear in `changed_pages`; pages outside the review whose receipt depended on a corrected page
(continuation or container closure, in the graph before the correction or in the one it
produced — units re-parented to a container on the page before reach that page only through
the new edge) lose that receipt and appear in `invalidated_pages`. The three lists are
disjoint and together are what needs a new packet and review, never an immediately stale
receipt.

### Override contract

A decision may carry `override` to correct that page. Only the fields below are supported; the
transaction rolls back on any violation.

```json
{
  "page": 12,
  "fingerprint": "<packet page fingerprint>",
  "viewed_original": true,
  "notes": "Display (3.2) was cut; caller/footnote link repaired.",
  "override": {
    "regions": [
      {"id": "eq-3-2", "kind": "math", "bbox": [72.0, 310.5, 318.2, 342.0], "display": true,
       "glyph_ids": ["b3-l4-s0-c0", "b3-l4-s0-c1"],
       "formula_conditions": [{"glyph_ids": ["b3-l4-s2-c0", "b3-l4-s2-c1", "b3-l4-s2-c2"], "source_text": "and"}]},
      {"id": "fig-3-1", "kind": "figure", "fragments": [{"bbox": [72, 400, 540, 610]}, {"bbox": [72, 620, 540, 700]}]},
      {"preserve_asset_id": "a-p0012-1f2e3d4c5b6a", "grouping_pending": false}
    ],
    "units": [
      {"unit_id": "p0012-b3", "kind": "paragraph", "bbox": [72, 280, 540, 300], "parent_id": "p0012-b3",
       "source_markdown": "Hence[^2] the norm {{asset:a-p0012-1f2e3d4c5b6a}} satisfies", "footnote_refs": ["p0012-b9"]},
      {"unit_id": "p0012-b3-displaypart2", "kind": "equation", "bbox": [72, 310, 320, 342], "parent_id": "p0012-b3",
       "source_markdown": "{{asset:eq-3-2}}", "equation_number": "3.2"},
      {"unit_id": "p0012-b3-displaypart3", "kind": "paragraph", "bbox": [72, 350, 540, 370], "parent_id": "p0012-b3",
       "source_markdown": "for all t."},
      {"unit_id": "p0012-visual-fig-3-1", "kind": "figure", "bbox": [72, 400, 540, 700],
       "source_markdown": "{{asset:fig-3-1}}"},
      {"unit_id": "p0012-b9", "kind": "footnote", "footnote_number": "2", "bbox": [72, 700, 540, 720],
       "parent_id": "p0012-b9", "source_markdown": "See the remark after Lemma 2."}
    ]
  }
}
```

- `page_canvas_bbox: [0, 0, width, height]` expands an unrotated origin-zero page in memory
  (see [Formula-contained language and page overflow](#formula-contained-language-and-original-page-overflow)).
- The override is the page's whole correction. It replaces the override the page's ledger
  already records (`source_overrides`), block by block, and it is what `source extract --replace` replays afterwards. A decision whose `override` omits a block the ledger records
  (`regions`, `units` or `page_canvas_bbox`) is refused — `page 64: the recorded override
  carries units (21 entries) that this override omits; carry it forward or set "units": null
  to drop it` — because correcting one formula with `regions` alone would otherwise retire the
  page's pinned `units` without a word. Carry the block forward, or state the drop with `null`
  (the ledger then records the override without it). An override of nothing but `null`
  blocks is not a correction: re-derive such a page with `source extract --pages N --replace --discard-overrides` and review a new packet.
- `regions` replaces the detector/native proposals for the page. Each region names `kind`
  (`math`, `table`, `code`, `figure` or `mixed-region`) and either a single `bbox` in PDF points
  or `fragments: [{bbox, glyph_ids?}, ...]` for one logical asset with several ordered fragments
  on the same page (cross-page elements use continuation links instead). Optional fields:
  `id` (`[A-Za-z0-9][A-Za-z0-9._-]*`, default `a-p<page>-<content hash>`), `glyph_ids` (explicit
  native glyph ownership; see the glyph corrections section), `display`, `grouping_pending`,
  `provenance` and `formula_conditions` (any `math` asset, inline or displayed; a `math` region
  that omits the key is declared automatically, an explicit list is kept as written). A region
  that names glyphs is exported from their paths; one that names none — `"glyph_ids": []` on a
  rule, a figure frame or a page number box, or no key at all — owns nothing and is a raw crop
  of its box, and keeps the `kind` it declares. Only when named glyphs cannot be isolated does
  the export fall back to a raw crop with `precise-export-unavailable:…` in its provenance and
  `grouping_pending` set; a `math` region then becomes a `mixed-region` (a raw crop is not an
  isolated expression), a `figure` or `table` stays what the reviewer said it is.
  `preserve_asset_id` reuses an unchanged existing asset of the same page and may only change
  `kind`, `display`, `grouping_pending` or `formula_conditions`. A preserved `math` asset that
  carries no declaration is declared automatically from its own glyphs, exactly as a region
  naming the same glyphs would be (provenance gains `auto-formula-conditions`); an existing
  declaration is kept and an explicit list, even an empty one, is the reviewer's decision. A
  preserved asset keeps its ID and crop directory whatever the change (see
  [Asset identity](source-processing.md#asset-identity)). A region may not import `latex`. Glyph ownership may not
  overlap between assets, and asset IDs may not collide across decisions or with assets of
  another page.
- `units` replaces the page's source units. Each unit needs `unit_id`
  (`[A-Za-z0-9][A-Za-z0-9._-]*`, unique across the project), `source_markdown` (prose with
  `{{asset:ID}}` placeholders and `[^n]` footnote calls) and `bbox`; optional `kind` (default
  `paragraph`; `footnote`, `heading`, `list_item`, `caption`, `equation`, `figure`, `table`,
  `note`, `bibliography`), `equation_number`, `footnote_number`, `footnote_refs`, `parent_id`,
  `continues_from_previous`, `continued_to_next`, `render_policy` (`include`/`omit`) and
  `translatable`. Across the page's units every page asset must be referenced exactly once. When
  `units` is omitted the units are re-derived from the regions with the normal structure
  assembly and inline-fragment coalescing. Inline fragments along a row are coalesced in this
  channel too: a `units` block may reference the coalesced asset (one ID, as the packet lists
  it) or its constituents; a coalesced asset the block does not reference is restored to the
  assets it was made of before the reference check. A recorded unit is filled with the same
  optional keys structure assembly passes before its `source_hash` is computed, so identical
  content hashes identically whichever channel wrote it: the final hash is
  `hash({prepared_source_hash, asset_content_hashes})` over the unit payload's hash and its
  assets' `content_sha256`, deterministic for the same content.
- A region `bbox` (or fragment `bbox`) is the target box: only owned glyph ink is padded by
  0.5pt, so a `fragment.bbox` copied from the packet reproduces the same fragment, `width`,
  `height`, `baseline` and `content_sha256`. Fragment dimensions derive from the 4-decimal
  `bbox`, so a re-derived fragment compares byte for byte.
- Footnote relationships are validated against the retained and replacement units together:
  unknown, non-footnote or duplicate targets and call numbers that do not match the referenced
  definitions reject the whole import.
- Same-page boundary repairs may retain stable IDs; changed content fingerprints invalidate old
  evidence and require fresh source review. Translations of removed unit IDs are retired to
  `translations/source-retired.jsonl` and removed from the current ledger in the same rollback
  transaction; batches containing changed units receive audit invalidations.

### Formula-contained language and original page overflow

A complete displayed cases formula may contain condition words such as “and … is odd”, and an
inline crop may hold an abbreviation such as `i.o.` or `a.s.`. Preparation declares them itself
(see [Prepared units and assets](source-processing.md#prepared-units-and-assets)); a reviewed region can also
declare `formula_conditions: [{glyph_ids: [...], source_text: "..."}]` on any `math` asset.
Each entry must match owned native glyphs in native order on one visual line; `source_text`
is compared ignoring whitespace, so TeX word gaps may be written as spaces. The source gate
still checks all undeclared prose and reports language the declarations miss as the
`undeclared-formula-language` finding. It additionally requires the independent page review's
`formula_conditions_checked`; the asset remains math, its source unit becomes translatable, and
translation QA requires a Chinese companion and rejects a no-language attestation. Empty
declarations are omitted from serialization to preserve existing source fingerprints.

An explicit page override `page_canvas_bbox: [0, 0, width, height]` may reveal preexisting
content-stream glyphs outside an unrotated PDF page's current box. Only the temporary in-memory
document is expanded. The original page image remains unchanged; the ledger adds a hashed
overflow image and the original page box, and original-context packets require both images.
Review must explicitly attest `overflow_canvas_checked`. Recovered glyphs participate in normal
ownership, immutable image fragments and source verification. No OCR or replacement symbols are
introduced.


### Source results

- `source inspect`: `source` path, `source_sha256`, `page_count`, `selected_pages`,
  `outline_entries`, `pages` objects (`page`, `text_characters`, `image_count`,
  `requires_manual_review`), and `scanned_or_empty_pages`.
- `source extract`: always `pages`, `prepared_pages`, `cached_pages`, `assets` (registry count),
  `requires_visual_review`, `document_structure`. A preparation run additionally returns
  `replayed_override_pages`, `redetected_override_pages`, `discarded_override_pages`,
  `reused_layout_pages`, `detected_layout_pages`, `retained_receipt_pages`, `invalidated_pages`,
  `pruned_asset_directories`, `layout_status`, `review_packet`, `visual_report`, `generator`.
  A cache-only result need not contain those extra fields.
- Source records are `derived/units.jsonl` ([SourceUnit](#model-sourceunit)) and
  `derived/fidelity-assets.jsonl` ([FidelityAsset](#model-fidelityasset)). Each fragment has project-relative image paths and an exact matching file-hash map;
  positive geometry is required. An omitted source unit must be nontranslatable. Sidebar ID/role
  occur together; title roles require heading kind. Callout kind applies only to note units.
- A source review receipt is bound to source, page fingerprint, original packet, visual report
  and decision via its stored digest. Keep its packet and referenced files. A verification receipt
  such as [PageVerificationReceipt](#model-pageverificationreceipt) is generated output, not a
  substitute submission format for source-review decisions.

### Translation contract

One [TranslationRecord](#model-translationrecord) per JSONL line; IDs must belong to the batch's
editable `translatable_unit_ids`, not its `read_only_unit_ids`. The source hash must equal the
current unit's source hash. Submission requires exactly the full editable unit set once each; missing, duplicate or
out-of-scope units and invalid image evidence are refused. A model-valid record alone does not establish QA or approval.

Translation records retain their unit's `source_hash`, references and an `image_evidence` map of
inspected original image path to SHA-256 from `original-images.json`. Supplementary translations
of image-contained language live in `asset_translations`, keyed by `asset_id`, using
`target_text`, `target_table` or `figure_labels`. Declare `language_present: false` with
explanatory `notes` only for an asset with no translatable natural language. Asset candidate
content never replaces the source reference ID. Do not put raw LaTeX in translated image
companions; preserve references and submit mathematical candidates through the independently
reviewed asset channel. Companion text, table cells and label mappings cannot contain asset
placeholders or live footnote calls; keep calls in the main translation and escape literal
notation or use code literals.

A resubmission that is semantically identical to the current record keeps its revision. When
only the `source_hash` binding changed, the record is rebound as `revised` and its audits are
invalidated. When only `image_evidence` changed, the receipt is updated in place: the translated
content is untouched, audits stay valid, and QA (which binds the receipt) simply becomes stale.

[TableData](#model-tabledata) requires nonempty rectangular string rows, matching `column_count`,
and `0 <= header_rows <= len(rows)`. [ReaderNote](#model-readernote) source URLs must use HTTPS.
[AssetTranslation](#model-assettranslation) with `language_present: false` requires nonempty notes.
Original-asset placeholders use `{{asset:ID}}`; source-owned references and evidence are validated
by the importer and QA, not created by the target text.

Minimal shape example (replace unit/source bindings and supply required image evidence before import; submit one record for every editable unit):

<!-- example-model: TranslationRecord -->
```json
{"unit_id":"p0001-u001","target_text":"示例译文。","source_hash":"SOURCE_HASH"}
```

### Audit issue contract

A representative [ReviewIssue](#model-reviewissue) JSONL record is shown below. The field table lists required fields and defaults:

```json
{
  "issue_id": "batch-r001",
  "batch_id": "p0001-p0010-b001",
  "unit_id": "p0003-u004-abcd1234",
  "severity": "major",
  "type": "meaning",
  "source_span": "exact source phrase",
  "target_span": "exact translated phrase",
  "explanation": "Why this changes or obscures the source meaning.",
  "suggested_revision": "A focused correction.",
  "confidence": 0.95,
  "reviewer": "fidelity-reviewer",
  "status": "open"
}
```

Severity:

- `blocker`: unusable or unsafe output, extensive missing content, corrupted structure.
- `major`: material mistranslation, omission, addition, technical error, or broken reference.
- `minor`: localized accuracy, terminology, or clarity defect.
- `suggestion`: optional polish that does not change correctness.

Type must be one of `meaning`, `omission`, `addition`, `terminology`, `technical`, `style`, `reference`, `number-unit`, or `format`.

`issue_id` only needs to be unique within the reviewer's own output: a coordinated `translation review import-set` replaces it with a canonical `audit-<hash>` id and stores the original under `source_issue_id`; `translation review resolve` accepts either.


A closed issue (`resolved`, `rejected`, `waived`) requires a nonempty `resolution` and
`resolved_at`. Packet imports canonicalize IDs and validate source/context scope. Empty issue
JSONL still records completed audit coverage. [AuditRun](#model-auditrun) stores coverage;
[QAReport](#model-qareport) stores deterministic checks. Their fingerprints must remain current.

<!-- example-model: ReviewIssue -->
```json
{"issue_id":"r001","batch_id":"b001","unit_id":"p0001-u001","severity":"major","type":"meaning","explanation":"The condition was reversed.","reviewer":"fidelity-reviewer"}
```

### Asset contract

**Packet bindings**

Structured candidate formats are bound to source kind: `math` accepts `latex`, `table` accepts
`table`, and `code` accepts `code`. Figures and `mixed-region` assets remain original images; use
a source review correction to establish a more precise kind before requesting transcription.
Free text is not a replacement format for these kinds. Asset task scope includes semantic
dependencies outside the requested batch, matching QA.

Asset-audit packets bind `render_manifest` (relative render-directory paths to SHA-256) and
`render_manifest_sha256` into the packet identity. The manifest covers the comparison HTML,
copied MathJax runtime and original SVG/PNG files (fragments prepared by the 0.6.0 build may also
carry a per-region `original.pdf`; it is no longer written, linked or copied, and `source extract --replace`
removes it from the directories it re-exports). Review submissions must echo both
`render_artifact_sha256` and `render_manifest_sha256` from the packet after inspecting the actual
artifact. Imports and subsequent status queries verify all dependencies. Old packets without this
manifest require a new audit and cannot retain verified status; their candidates and history
remain available. Rebuilding a damaged render creates a new packet identity and requires a fresh
review, never silently repairs an old approval.

**Candidate and review envelopes**

Submit transcription through `assets submit PROJECT INPUT`; the envelope includes `packet_id`,
`author_task_id`, the packet's dispatch `model` and `reasoning_effort` echoed verbatim, `image_evidence`,
`candidates` and available `usage` (otherwise `null`); an optional `served_model_label` records the model the
host environment reported under that dispatch value (stored as is, unverified, never gated). A packet that
records no model or effort dispatched on the host's own default, so the echo of the absent field is omitted. Candidates name `asset_id`, `format` and `content`; `status` is
`candidate` or `unresolved`, with `notes` and `semantic_uncertainty` as needed. LaTeX content is a
math body without dollar delimiters; table content uses a rectangular `rows` array of cell
strings. Both candidate and review envelopes record actual viewing in `image_evidence` using the
packet's `required_images` path/hash map.

An asset reviewer has a different `reviewer_task_id` and returns `render_artifact_sha256` and
`render_manifest_sha256` plus one decision per asset, with `candidate_sha256`, `verdict`
(`accept`, `reject` or `unresolved`), `visual_checked` and `render_checked`; import using
`assets import-review PROJECT INPUT --confirm-visual-review` only when those checks were
performed. Stored review payloads must match their `review_sha256` before decisions are consumed,
imports replayed or revision context built; corrupt evidence cannot grant verified status. An
indexed review that cannot be validated blocks QA until renewed independent review; rebuilding its
audit packet uses a new identity and preserves the damaged historical file. Use
`assets status PROJECT` for the remaining queue.


Candidate envelope fields are [AssetSubmission](#model-assetsubmission) and
[AssetCandidateInput](#model-assetcandidateinput); review fields are
[AssetReviewSubmission](#model-assetreviewsubmission) and [AssetReviewItem](#model-assetreviewitem).
The candidate model accepts `text` syntactically, but the current kind/format importer does not
accept it as a replacement for math/table/code assets. Table candidate content is a JSON object
with nonempty rectangular string `rows`; LaTeX/code content is a string. Kind/format validation
is stricter than the union in the field table.

<!-- example-model: AssetCandidateInput -->
```json
{"asset_id":"a-p0001-example","format":"latex","content":"x^2","status":"candidate"}
```

### Task contract

Task creation accepts exactly one scope selector. Supported combinations are:

| Stage | Selector | Additional requirements | Result encoding |
| --- | --- | --- | --- |
| source-review | pages or batch-ids | Source packet; visual confirmation at receive | Source review JSON |
| translate, revise | batch-ids | Exactly one batch, no lens | Translation JSONL |
| audit | batch-ids | One fidelity/technical/chinese-style lens | ReviewIssue JSONL, possibly empty |
| transcribe, asset-audit | asset-ids or batch-ids | Current applicable assets; visual confirmation for audit | Asset submission/review JSON |
| scout | pages | Nonempty objective | JSON with findings, proposed_rules, unresolved lists |
| terminology | pages | Nonempty objective | JSON with proposals, unresolved lists |
| external-recheck | batch-ids | Exactly one batch, no lens, current external review, subagent claim | External review result JSON |

`--revision-notes` on task creation is allowed only for transcribe with explicit asset IDs.

`result.json` is the default filename even when the result encoding is JSONL. No additional
outer task envelope is wrapped around the domain result. Scout/terminology list elements are
proposal content; the receiver checks required list fields, saves them and reports
`proposal_received: true, approved: false` without applying changes.

[TaskEnvelope](#model-taskenvelope) is written to `.littrans/work/tasks/TASK_ID/task.json`.
`start.md` and `instructions/` accompany it. Envelope `inputs` are project-relative path/hash
bindings; `instructions` paths are relative to the saved instructions directory. Other packet
paths resolve from PROJECT. Task IDs hash the envelope excluding task_id; do not edit envelopes.

Status fields: `task_id`, `stage`, `state` (pending/claimed/imported), `executor`,
`result_available`, `handoff`; claimed state adds `mode` and `independence`;
imported state adds `result_sha256` and domain `outcome`. Replay can add `replayed: true`.
List status wraps these objects in `tasks`. A result can exist without being imported.

Receive validates current source, snapshot, packet files, instructions and translation batch
bindings. The identical imported byte stream is replayable; a different one is refused. Task
claims prevent conflicting declared writers and require distinct relevant reviewer executors;
they do not lock out manual writes or other machines. Import does not establish domain approval.

### Workflow contract

`workflow packet` stages are `source-review`, `translate`, `revise`, `audit`, `transcribe` and
`asset-audit`. Translation/revision is one batch; one audit reviewer handles at most three batches
and exactly one lens. Ordered sets must not overlap and must be consecutive within each series.
[WorkflowPacketManifest](#model-workflowpacketmanifest) binds files, hashes, dispatch values,
per-batch unit coverage and dependency context. Source-review has its own packet/dispatch response.
Asset stages use asset packets. A no-pending-work response is not a completed model result.

Wave limits (default / maximum): Codex 3/3, Cursor 6/9, Claude 3/6, Qoder 3/6, OpenCode 3/6,
generic 1/3. Unknown or mixed auto-detection uses generic; select OpenCode explicitly.
`start-at` / `through` constrain batch-series coordination, not physical page numbers. Necessary
dependency batches may be scheduled outside those bounds and appear separately from requested IDs.

Workflow output separates ready tasks, optional asset tasks and per-batch lane state. Coverage
gaps inside the selected span refuse coordination; unbatched pages outside it are reported.
Audit stale reasons are `context-changed`, `dependency-changed`, `unit-changed`, `invalidated`,
`closure-incomplete`, `context-units-removed`; context changes may include per-part line counts.
No packet creation or task-readiness report runs a model.

### External review contract

**Configuration**

Configure reviewers by stable ID in `settings.yaml` through `config apply`:

```yaml
external_review:
  enabled: true
  reviewers:
    primary:
      driver: codex-cli
      model: YOUR_MODEL
      model_identity: null
      effort: high
    backup:
      driver: opencode-cli
      model: PROVIDER/MODEL
      model_identity: null
      effort: high
  primary: primary
  fallbacks: [backup]
  domain_expertise: null
  timeout_seconds: 330
  recheck:
    confidence_below: 0.9
    severities: [blocker, major]
```

Apply this group as part of a complete candidate settings file. Bind executable paths using
`config set PROJECT commands.primary PATH --local`; otherwise use the driver PATH command.
Model identity comes from CLI metadata. Explicit null effort uses the driver's default.
A finding requires recheck when confidence is below the threshold **or** severity belongs
to the configured set. Inconclusive overall verdicts also require recheck.

| Driver | Model and effort mapping |
| --- | --- |
| `codex-cli` | `--model MODEL`, `-c model_reasoning_effort="EFFORT"` |
| `opencode-cli` | OpenCode 2.x: `--model provider/model#variant`; `effort` selects the variant |
| `claude-code` | `--model MODEL --effort EFFORT`; fast mode must remain off |
| `antigravity` | `--model MODEL --effort EFFORT`; some models do not accept effort |
| `cursor-cli` | Exact model IDs encode effort; omit separate `effort` and `fast` |

An embedded OpenCode `#variant` may replace `effort`; when both are supplied they must agree.
Custom variants are supported. `--thinking` displays thinking and does not select effort.
Use `model_identity` when a dispatch alias differs from the model reported by CLI metadata.
Identity verification does not accept model self-reports or requested arguments as evidence.
Requested and actual effort are separate; unavailable actual effort remains unknown.

Set optional `domain_expertise` for project-specific subject expertise. It is part of the
isolated packet and its fingerprint. Otherwise expertise follows the document brief.

The recheck returns a JSON object with `verdict` (`accepted`, `changes-requested`, `inconclusive`),
`summary`, and `issues`. Each issue requires `unit_id`, `severity` (`blocker`, `major`, `minor`,
`suggestion`), `type` (`meaning`, `omission`, `addition`, `terminology`, `technical`, `style`,
`reference`, `number-unit`, `format`), exact `source_span`, exact `target_span`, `explanation`,
`suggested_revision` (empty string if none), and numeric `confidence` between 0 and 1.

The coordinator decision JSON includes `run_id`, `task_id`, `verdict`, an evidence-based
`reason`, and `issues`, mapping every external/recheck issue ID to an `action`
(`accept`, `reject`, `inconclusive`) and evidence-based `reason`. Agreement also requires a
recorded decision. Conflicts remain inconclusive until adjudicated by the coordinator.
Accepting a finding keeps it open until corrected; rejecting an evidenced false positive closes it.


`issues` in the external/recheck response is an array; `issues` in an adjudication is an object
keyed by the union of the external run's issue IDs and recheck finding IDs. Obtain those IDs
from external-status. Extra/missing issue keys are refused. An inconclusive action requires an
inconclusive overall verdict. An accepted verdict cannot leave any nonsuggestion finding open.
A rejected finding gets a rejected disposition; accepting it leaves it open unless already resolved.

External response keys must be exactly `verdict`, `summary`, `issues`; each issue must contain
exactly the listed issue fields. The trimmed summary must have at least 10 characters and two
word tokens. A nonempty suggested revision must differ from the target span. `accepted` cannot
contain nonsuggestion issues; `changes-requested` requires at least one nonsuggestion issue.

Minimal no-findings external/recheck result:

<!-- example-validator: external-result -->

```json
{"verdict":"accepted","summary":"No substantive defects found.","issues":[]}
```

Adjudication shape for a current no-findings run/recheck (replace IDs with external-status values):

```json
{"run_id":"RUN_ID","task_id":"TASK_ID","verdict":"accepted","reason":"Both reviews agree with the cited source evidence.","issues":{}}
```

External run/attempt records are generated evidence, described by
[ExternalReviewRun](#model-externalreviewrun) and [ExternalReviewAttempt](#model-externalreviewattempt).
They separate requested model/effort from observed identity, usage, failures and elapsed time.
A stored historical second-opinion record does not create a current CLI gate.

### Render results

`translation render` returns output path strings `markdown`, `html`, `quality`, `unresolved`,
`render_qa`, optionally `external_review`, and optionally `originals_only_reason` (`requested`
or `no-transcription-candidates`). These files live under output/; rendering also copies supporting
images/runtime assets. Read the quality and render-QA artifacts to determine output status.
Draft output retains draft status. Structural render-QA failures prevent successful publication.

### Diagnostic and result objects

The command entries link to record tables when stdout is a model. Other result objects use the
following keys. Paths are strings, counts are integers, flags are booleans and digest maps map
strings to SHA-256 strings. Optional fields appear only when their corresponding operation ran;
consumers should not require cache-hit output to contain fresh-run telemetry.

| Producer | JSON object fields |
| --- | --- |
| layout status | python and model (path or null), cache_root, mineru_version (string or null), identity (object or null), packaged_app, ok, reason (string or null), install_command; legacy_cache may be reported |
| layout install | Layout status plus installed, smoke_test, model_source, weights_downloaded, repaired, adopted_legacy_weights (path or null) |
| project models | host; supports with model, reasoning_effort, agent_effort and optional project_agent_config; roles keyed by role; advisories array |
| project scaffold | project_root, repo_root, created/kept/refreshed path arrays, plugin_owned path array |
| project tracked | project_root, git_toplevel, must_track/must_ignore/tracked/ignored/gap counts, live_source_packets and problems |
| source probe | profile path, pages, new_pages, status, next |
| source rescope | profile path, packet_id, pages, scoped_rules/unchanged_rules names, page_rules count, applied |
| source verify, current fidelity source | passed, errors array, verified_pages, requested_pages, dependency_pages, receipt_packets, visual_report (null for this implementation) |
| assets submit | packet_id, candidate_count, candidate_sha256 (asset-ID/digest map), usage (object or null), replayed |
| assets status | assets map with state, candidate_sha256 (or null), semantic_uncertainty, reviewer_uncertainty and conditional diagnostics; counts by state |
| context glossary lookup | selection (selector, unit_count when unit-scoped, scope_applied), approved array, reference map of kind to entry arrays, approved_total, reference_total |
| context glossary check | prepared_units; approved with total/never_matched; reference with total/by_kind/never_matched; candidates with total |
| external-migrate | changed=false, applied=false and config next_actions for v7; rejects historical projects |
| external review dry-run | schema_version=5, executed=false, batch_id, translation_fingerprint, scope, covered_unit_ids, read_only_context_unit_ids, packet_path, packet_sha256, context_fingerprint, page_sha256s, prompt_version, calls |

Layout `identity` reports configured_python, resolved_python, pyvenv_home, pyvenv_version,
version, executable, resolved_executable, base_executable and import_error. Missing/unavailable
values remain null. A ready receipt does not override a failed current interpreter probe.

Asset states include `transcribe`, `asset-audit`, `verified`, `fallback`. Semantic uncertainty is
independent of whether a fallback image exists. State queries validate indexed evidence before
reporting verified status.

Workflow next returns `stage`, `batch_ids`, `host`, `limit`, `start_at`, `through`, `ready_tasks`,
`optional_asset_tasks`, `audit_stale`, `schedule`, `unbatched_pages`, `dispatch_advisories`; an active
wave also includes `requested_batch_ids`. Complete output can still include optional asset work.
Workflow status returns `batch_ids`, `host`, `stage`, `stages`, `audit_stale`, `reading_complete`,
`assets` (per-batch lanes), `ready_tasks`, `optional_asset_tasks`, `assets_complete`, `complete`,
`unbatched_pages`, `dispatch_advisories`. Ready-task objects describe the applicable domain packet
and dispatch. Their stage-specific inputs are the same contracts used by packet commands. An audit
batch yields one ready task per lens still missing current coverage (all three when none is
recorded), each with `lens` and that lens's resolved `model` / `reasoning_effort`; create one audit
task per entry. On OpenCode, ready tasks for dispatchable stages include `native_agent`. The wave
limit still counts batches, not lens tasks.

Workflow metrics fields are `batch_ids`, `history_records`, `semantic_noop_records`,
`semantic_noop_ratio`, `legacy_packet_bytes`, `generated_packet_bytes`,
`generated_packet_allocation`, `page_receipts`, `audit_runs`, `audit_evidence_rows`,
`logical_audit_calls`, `external_runs`, `external_attempts`, `external_provider_turns`,
`external_cached_input_tokens`, `external_non_cached_input_tokens`, `external_duration_seconds`,
`external_cost_usd`, `external_usage` (token/call totals). Packet allocation is
`equal-per-batch-leading-remainder`; evidence row counts and logical calls are distinct.

External status `recheck` contains `required`, `complete`, `unit_ids`, `result`, `adjudication`
and `verdict`. Missing result/decision is null. Once a valid result exists it may include
`decision_template`; use its exact issue IDs when constructing adjudication input. Missing or stale
required evidence prevents approval, including when the original external verdict was accepted.

### Asset packet fields

An asset packet contains `stage`, `host`, `model`, `reasoning_effort`, `fresh_context`,
`prompt_version`, `allowed_formats` (asset ID to permitted format), `asset_ids`, `assets`,
`asset_fingerprints`, `context_units`, `context_documents`, `instructions`, `required_images`
and `packet_id`. Context units are source context, not extra editable asset IDs.

Transcription revisions add `revision_notes` and `revision_context`; recovery may add
`candidate_recovery`. Audit packets add `candidates`, `renderer_sha256`, `render_manifest`,
`render_manifest_sha256` and `render_artifact` with path/sha256. Rebuilt evidence can additionally
record previous review/render packet IDs. Importers compare the saved packet and its current
files rather than trusting echoed hashes alone.

### Record field tables

These tables cover the serialized fields and nested types of public records. `required` means no
model default. Empty containers default to new empty containers. Runtime defaults are identified
explicitly. Type alternatives use “or”; object keys are strings unless stated otherwise.
Named types link to their own table. Constraints here are structural; the domain contracts above
also apply. Models shown only as generated output are not writable approval interfaces.

### Model ProjectConfig

Internal runtime view, assembled from the v7 manifest, saved settings and state. This is not
the `project.yaml` file format. See [configuration](configuration.md) and `config schema` for
persistent settings; generated project schemas describe the separate manifest.

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | integer | no | `7` | Runtime project schema version; distinct from the plugin release version. |
| `project_id` | string | yes | `required` | Project identifier assigned at initialization. |
| `title` | string | yes | `required` | Human-readable document title. |
| `source_path` | string | yes | `required` | Absolute or project-relative PDF path. |
| `source_sha256` | string | yes | `required` | Original PDF SHA-256. |
| `source_pages` | integer | yes | `required` | PDF page count recorded at initialization. |
| `profile` | string | yes | `required` | Document profile name or file path. |
| `record_root_relative` | string or null | no | `null` | Portable path from the project to its scaffold record root; absent in older projects. |
| `source_language` | string | no | `"en"` | Source language tag. |
| `target_language` | string | no | `"zh-CN"` | Target language tag. |
| `rights_status` | string | no | `"private-research-only"` | Project rights designation; does not grant publication rights. |
| `external_review` | [ExternalReviewConfig](#model-externalreviewconfig) or null | no | `null` | Optional external reviewer configuration. |
| `agent_models` | map of map of [RoleDispatch](#model-roledispatch) | no | `bundled host policy` | Per-host role dispatch policy: translate, transcribe, audit, asset-audit and source-review, each with its own model and reasoning_effort. Each model is the dispatch value handed to that host's task launcher, a host alias or a concrete id as the host defines; which model the host serves under it is the host's own configuration and is never verified here. An unset role, model or effort is supported and follows the host's own default subagent behaviour. Claude Code takes no per-dispatch effort: the LitTrans agents' frontmatter sets it. |
| `status` | [ProjectStatus](#enum-projectstatus) | no | `"initialized"` | Stored state; importers control approval transitions. |
| `extractor_version` | string | no | `"2"` | Recorded extraction implementation version. |
| `created_at` | string | no | `current UTC time` | UTC creation timestamp. |
| `updated_at` | string | no | `current UTC time` | UTC update timestamp. |

### Model RoleDispatch

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `model` | string or null | no | `null` | Dispatch model for this role: the value passed to the host's task launcher (a host alias or a concrete id). Not the served model. Unset follows the host's default subagent model. |
| `reasoning_effort` | string or null | no | `null` | Dispatched reasoning effort for this role, independent of every other role. Unset follows the host's default. Not applied on Claude Code, whose LitTrans agents fix their effort in their frontmatter. |

### Model ExternalReviewConfig

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | 2 | no | `2` | Serialized record schema version; distinct from the plugin release version. |
| `enabled` | boolean | no | `true` | Whether external review is enabled for the project. |
| `reviewer` | [ExternalReviewerConfig](#model-externalreviewerconfig) | yes | `required` | Reviewer identity, or the configured reviewer object for external settings. |
| `fallbacks` | array of [ExternalReviewerConfig](#model-externalreviewerconfig) | no | `[]` | Ordered reviewer configurations tried after invocation failures. |
| `recheck` | [ExternalRecheckConfig](#model-externalrecheckconfig) | no | `{"confidence_below": 0.9, "severities": ["blocker", "major"]}` | Host-recheck trigger configuration. |
| `domain_expertise` | string or null | no | `null` | Nonempty optional subject-expertise instruction included in packet fingerprints. |

### Model ExternalReviewerConfig

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `id` | string | yes | `required` | Stable identifier within the owning registry/configuration. |
| `driver` | [ExternalReviewDriver](#enum-externalreviewdriver) | yes | `required` | External CLI adapter. |
| `command` | string | yes | `required` | Executable name or path invoked by the external-review driver. |
| `model` | string | yes | `required` | Dispatch value passed to the provider CLI's model option. |
| `model_identity` | string or null | no | `null` | Concrete model the host must report as served for `model`. Set it when `model` is a host alias (for example `sonnet`) that the host routes to another model; when unset, `model` itself is the identity that host evidence must match. |
| `effort` | string or null | no | `null` | Requested external CLI reasoning effort; subject to driver restrictions. |
| `fast` | boolean or null | no | `null` | Claude fast-mode setting; true is refused, and other drivers must omit it. |

### Model ExternalRecheckConfig

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `confidence_below` | number | no | `0.9` | {"maximum": 1, "minimum": 0} |
| `severities` | array of [Severity](#enum-severity) | no | `["blocker", "major"]` | Issue severities that trigger recheck. |

### Model StructureProfile

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | 1 | no | `1` | Serialized record schema version; distinct from the plugin release version. |
| `source_sha256` | string | yes | `required` | {"pattern": "^[a-f0-9]{64}$"} Original PDF SHA-256. |
| `pages` | array of integer | yes | `required` | {"minItems": 1} |
| `status` | enum ["draft", "reviewed"] | no | `"draft"` | Stored state; importers control approval transitions. |
| `observations` | array of object | no | `[]` | Source observations collected by the probe. |
| `handling_rules` | map of string | no | `{}` | Base rule category to guidance text. |
| `page_rules` | array of [PageRules](#model-pagerules) | no | `[]` | Ordered scoped additions to base guidance. |
| `inspected_pages` | array of integer | no | `[]` | PDF pages actually inspected for the profile. |
| `review_notes` | string | no | `""` | Explanation of the profile review. |

### Model PageRules

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `label` | string | no | `""` | Human-readable label; excluded from effective page guidance. |
| `pages` | string | yes | `required` | PDF page numbers or scoped page expression, as indicated by type. |
| `handling_rules` | map of string | yes | `required` | {"minProperties": 1} |

### Model SourceUnit

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | integer | no | `3` | Serialized record schema version; distinct from the plugin release version. |
| `unit_id` | string | yes | `required` | Stable source unit ID. |
| `kind` | [UnitKind](#enum-unitkind) | yes | `required` | Source semantic kind or asset kind, according to the owning record. |
| `page` | integer | yes | `required` | One-based PDF page number. |
| `bbox` | tuple [number, number, number, number] | yes | `required` | {"maxItems": 4, "minItems": 4} |
| `source_text` | string | yes | `required` | Source text retained for the unit or declaration. |
| `source_hash` | string | yes | `required` | Current source-unit content binding. |
| `asset_content_hashes` | map of string | no | `{}` | Referenced asset IDs mapped to content digests, bound into source identity. |
| `source_markdown` | string or null | no | `null` | Preserved prose with original-asset placeholders and footnote calls. |
| `parent_id` | string or null | no | `null` | Logical container's source-unit ID; preserves group dependencies. |
| `sidebar_id` | string or null | no | `null` | Shared identity of a sidebar container. |
| `sidebar_role` | [SidebarRole](#enum-sidebarrole) or null | no | `null` | Title/body role; must occur together with sidebar_id. |
| `callout_kind` | [CalloutKind](#enum-calloutkind) or null | no | `null` | Admonition kind, permitted only on note units. |
| `translatable` | boolean | no | `true` | Whether the source unit belongs to editable translation scope. |
| `render_policy` | [RenderPolicy](#enum-renderpolicy) | no | `"include"` | Include the unit or omit it from reading output; omitted units cannot be translatable. |
| `protected_tokens` | array of string | no | `[]` | Source tokens that QA expects to preserve. |
| `asset_refs` | array of [AssetRef](#model-assetref) | no | `[]` | Legacy/source asset references associated with the unit. |
| `fragments` | array of [SourceFragment](#model-sourcefragment) | no | `[]` | Ordered original-source fragments belonging to this logical element. |
| `latex` | string or null | no | `null` | Stored mathematical representation; not a source-region override field. |
| `equation_number` | string or null | no | `null` | Source equation label retained separately from content. |
| `footnote_number` | string or null | no | `null` | Printed number/label on a footnote definition. |
| `footnote_refs` | array of string | no | `[]` | Unique IDs of referenced footnote units. |
| `math_status` | [SemanticStatus](#enum-semanticstatus) or null | no | `null` | Verification state of the mathematical representation. |
| `code_language` | string or null | no | `null` | Language identifier for code rendering. |
| `table` | [TableData](#model-tabledata) or null | no | `null` | Structured source table when present. |
| `continues_from_previous` | boolean | no | `false` | Unit continues a sentence from the prior adjacent page. |
| `continued_to_next` | boolean | no | `false` | Unit's sentence continues onto the next adjacent page. |
| `figure_labels` | array of [FigureLabel](#model-figurelabel) | no | `[]` | Source/target label pairs for figure language. |
| `visual_text_status` | [SemanticStatus](#enum-semanticstatus) or null | no | `null` | Verification state of image-contained source text. |
| `verification_status` | [SemanticStatus](#enum-semanticstatus) | no | `"unverified"` | Source unit's recorded verification state. |
| `confidence` | number | yes | `required` | {"maximum": 1, "minimum": 0} |
| `status` | [ProjectStatus](#enum-projectstatus) | no | `"extracted"` | Stored state; importers control approval transitions. |

### Model FidelityAsset

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | 6 | no | `6` | Serialized record schema version; distinct from the plugin release version. |
| `id` | string | yes | `required` | Stable identifier within the owning registry/configuration. |
| `kind` | enum ["math", "table", "code", "figure", "mixed-region"] | yes | `required` | Source semantic kind or asset kind, according to the owning record. |
| `source_sha256` | string | yes | `required` | {"pattern": "^[a-f0-9]{64}$"} Original PDF SHA-256. |
| `content_sha256` | string | yes | `required` | {"pattern": "^[a-f0-9]{64}$"} |
| `content_identity_version` | enum [1, 2] | no | `1` | Asset content-digest algorithm version; default version may be omitted on serialization. |
| `fragments` | array of [FidelityFragment](#model-fidelityfragment) | yes | `required` | {"minItems": 1} |
| `grouping_pending` | boolean | no | `false` | Whether the original region still needs a grouping decision. |
| `display` | boolean | no | `false` | Whether the asset is displayed rather than inline. |
| `provenance` | array of string | no | `[]` | Recorded preparation/export decisions. |
| `formula_conditions` | array of [FormulaCondition](#model-formulacondition) | no | `[]` | Source-native language declared within math assets. |

### Model FidelityFragment

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `page` | integer | yes | `required` | {"minimum": 1} |
| `bbox` | tuple [number, number, number, number] | yes | `required` | {"maxItems": 4, "minItems": 4} |
| `png_path` | string | yes | `required` | Project-relative raster evidence path. |
| `svg_path` | string | yes | `required` | Project-relative vector evidence path. |
| `pdf_path` | string or null | no | `null` | Optional legacy per-region PDF path; new exports omit it. |
| `glyph_ids` | array of string | no | `[]` | Owned native PDF glyph identifiers in source order. |
| `width` | number | yes | `required` | {"exclusiveMinimum": 0} |
| `height` | number | yes | `required` | {"exclusiveMinimum": 0} |
| `baseline` | number or null | no | `null` | Optional baseline used for inline placement. |
| `dpi` | integer | no | `300` | Raster export resolution. |
| `export_method` | enum ["raw-region", "explicit-glyph-paths-v1", "explicit-glyph-paths-v2"] | no | `"raw-region"` | Original glyph/region export method. |
| `file_sha256` | map of string | no | `{}` | Evidence paths mapped to SHA-256. |

### Model FormulaCondition

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `glyph_ids` | array of string | yes | `required` | {"minItems": 1} |
| `source_text` | string | yes | `required` | {"minLength": 1} |

### Model BatchManifest

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | integer | no | `1` | Serialized record schema version; distinct from the plugin release version. |
| `batch_id` | string | yes | `required` | {"pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"} |
| `project_id` | string | yes | `required` | Project identifier assigned at initialization. |
| `pages` | array of integer | yes | `required` | PDF page numbers or scoped page expression, as indicated by type. |
| `unit_ids` | array of string | yes | `required` | Source units in the record's selected scope. |
| `translatable_unit_ids` | array of string | yes | `required` | Editable translation units; submission must cover this exact set. |
| `read_only_unit_ids` | array of string | no | `[]` | Context-only units, disjoint from editable scope. |
| `frozen_scope` | boolean | no | `false` | Refresh preserves the explicit selected unit set. |
| `source_words` | integer | yes | `required` | Source-word count for batching. |
| `created_at` | string | no | `current UTC time` | UTC creation timestamp. |

### Model TranslationRecord

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | integer | no | `2` | Serialized record schema version; distinct from the plugin release version. |
| `unit_id` | string | yes | `required` | Stable source unit ID. |
| `target_text` | string | yes | `required` | Translated Markdown/prose, retaining required source references. |
| `target_table` | [TableData](#model-tabledata) or null | no | `null` | Optional rectangular translated table. |
| `figure_labels` | array of [FigureLabel](#model-figurelabel) | no | `[]` | Source/target label pairs for figure language. |
| `source_hash` | string | yes | `required` | Current source-unit content binding. |
| `image_evidence` | map of string | no | `{}` | Viewed original image path to expected SHA-256. |
| `asset_translations` | array of [AssetTranslation](#model-assettranslation) | no | `[]` | Image-language companions keyed by original asset ID. |
| `revision` | integer | no | `1` | {"minimum": 1} |
| `reader_note` | [ReaderNote](#model-readernote) or null | no | `null` | Optional supplemental note with source attribution. |
| `code_annotations` | array | no | `[]` | Saved policy or supplemental code annotations; see configuration reference. |
| `term_proposals` | array of [TermProposal](#model-termproposal) | no | `[]` | Unapproved terminology proposals from the translator. |
| `uncertainties` | array of string | no | `[]` | Unresolved translation questions recorded with the submission. |
| `status` | [ProjectStatus](#enum-projectstatus) | no | `"draft"` | Stored state; importers control approval transitions. |
| `updated_at` | string | no | `current UTC time` | UTC update timestamp. |

### Model AssetTranslation

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `asset_id` | string | yes | `required` | Stable original-asset identifier. |
| `language_present` | boolean | no | `true` | Whether the original image contains translatable natural language. |
| `target_text` | string | no | `""` | Translated Markdown/prose, retaining required source references. |
| `target_table` | [TableData](#model-tabledata) or null | no | `null` | Optional rectangular translated table. |
| `figure_labels` | array of [FigureLabel](#model-figurelabel) | no | `[]` | Source/target label pairs for figure language. |
| `notes` | string | no | `""` | Explanatory notes; required by some domain conditions described above. |

### Model TableData

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `rows` | array of array of string | yes | `required` | Nonempty rectangular array of string cells. |
| `header_rows` | integer | no | `1` | {"minimum": 0} |
| `column_count` | integer | yes | `required` | {"minimum": 1} |

### Model FigureLabel

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `source` | string | yes | `required` | Original source wording. |
| `target` | string or null | no | `null` | Proposed/translated target wording, nullable where the type permits. |

### Model ReaderNote

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `text` | string | yes | `required` | Reader-note text. |
| `sources` | array of string | no | `[]` | HTTPS source URLs supporting a reader note. |
| `accessed_at` | string or null | no | `null` | Optional source access timestamp. |

### Model TermProposal

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `source` | string | yes | `required` | Original source wording. |
| `target` | string | yes | `required` | Proposed/translated target wording, nullable where the type permits. |
| `reason` | string or null | no | `null` | Evidence supporting a proposal or decision. |

### Model ReviewIssue

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | integer | no | `1` | Serialized record schema version; distinct from the plugin release version. |
| `issue_id` | string | yes | `required` | Canonical issue identity (or reviewer-supplied identity before packet import). |
| `source_issue_id` | string or null | no | `null` | Original reviewer ID retained after canonicalization. |
| `batch_id` | string | yes | `required` | {"pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"} |
| `unit_id` | string | yes | `required` | Stable source unit ID. |
| `severity` | [Severity](#enum-severity) | yes | `required` | Finding severity; gates apply the documented severity rules. |
| `type` | [IssueType](#enum-issuetype) | yes | `required` | Finding category. |
| `source_span` | string or null | no | `null` | Source excerpt identifying the finding. |
| `target_span` | string or null | no | `null` | Target excerpt identifying the finding. |
| `explanation` | string | yes | `required` | Evidence explaining the finding. |
| `suggested_revision` | string or null | no | `null` | Proposed localized correction, not an applied translation edit. |
| `confidence` | number | no | `1.0` | {"maximum": 1, "minimum": 0} |
| `reviewer` | string | yes | `required` | Reviewer identity, or the configured reviewer object for external settings. |
| `status` | [IssueStatus](#enum-issuestatus) | no | `"open"` | Stored state; importers control approval transitions. |
| `resolution` | string or null | no | `null` | Explanation of a closed issue disposition. |
| `resolved_at` | string or null | no | `null` | Closure timestamp; required for a non-open issue. |

### Model AuditRun

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | integer | no | `1` | Serialized record schema version; distinct from the plugin release version. |
| `run_id` | string | yes | `required` | Review-run identity. |
| `batch_ids` | array of string | yes | `required` | Ordered batch IDs covered by this record. |
| `reviewer` | string | yes | `required` | Reviewer identity, or the configured reviewer object for external settings. |
| `lens` | string | yes | `required` | Audit lens; required and validated for audit packets/runs. |
| `scope` | [ReviewScope](#enum-reviewscope) | no | `"full"` | Full or incremental review coverage. |
| `base_run_id` | string or null | no | `null` | Prior run used as the incremental baseline. |
| `packet_id` | string or null | no | `null` | Identity of the saved domain packet. |
| `unit_fingerprints` | map of string | yes | `required` | Unit IDs mapped to review-input fingerprints. |
| `context_fingerprint` | string or null | no | `null` | Digest of review context. |
| `shared_context_fingerprint` | string or null | no | `null` | Digest of shared brief, style and relevant terminology. |
| `shared_context_parts` | map of object or null | no | `null` | Per-part hashes and line counts for context-change diagnostics. |
| `context_unit_ids` | array of string | no | `[]` | Read-only dependency units reviewed with the selected scope. |
| `issue_ids` | array of string | no | `[]` | Canonical findings produced by this run. |
| `reviewed_at` | string | no | `current UTC time` | UTC review timestamp. |

### Model QAReport

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | integer | no | `2` | Serialized record schema version; distinct from the plugin release version. |
| `batch_id` | string | yes | `required` | {"pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"} |
| `passed` | boolean | yes | `required` | Result of the deterministic verification/QA checks. |
| `translation_fingerprint` | string | yes | `required` | Digest of the translation state checked. |
| `qa_context_fingerprint` | string or null | no | `null` | Digest of the current QA dependencies. |
| `errors` | array of [QAItem](#model-qaitem) | no | `[]` | Blocking deterministic findings. |
| `warnings` | array of [QAItem](#model-qaitem) | no | `[]` | Nonblocking deterministic findings. |
| `checked_at` | string | no | `current UTC time` | UTC verification timestamp. |

### Model QAItem

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `code` | string | yes | `required` | Machine-readable diagnostic code. |
| `severity` | string | yes | `required` | Finding severity; gates apply the documented severity rules. |
| `message` | string | yes | `required` | Human-readable diagnostic explanation. |
| `unit_id` | string or null | no | `null` | Stable source unit ID. |

### Model WorkflowPacketManifest

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | integer | no | `2` | Serialized record schema version; distinct from the plugin release version. |
| `packet_id` | string | yes | `required` | {"pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"} Identity of the saved domain packet. |
| `stage` | string | yes | `required` | Domain task/packet stage. |
| `batch_ids` | array of string | yes | `required` | {"maxItems": 9, "minItems": 1} |
| `lens` | string or null | no | `null` | Audit lens; required and validated for audit packets/runs. |
| `host` | string or null | no | `null` | Resolved coordination host. |
| `model` | string or null | no | `null` | Dispatch model for the stage from saved settings agents.<host>: the value passed to the host's task launcher (alias or concrete id, host-specific), echoed by submissions. Not the served model. |
| `reasoning_effort` | string or null | no | `null` | Dispatched reasoning effort from saved settings agents.<host>, echoed by submissions. |
| `unit_ids` | array of string | yes | `required` | Source units in the record's selected scope. |
| `unit_fingerprints` | map of string | yes | `required` | Unit IDs mapped to review-input fingerprints. |
| `batch_unit_ids` | object | no | `{}` | {"patternProperties": {"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$": {"items": {"type": "string"}, "type": "array"}}} |
| `batch_context_unit_ids` | object | no | `{}` | {"patternProperties": {"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$": {"items": {"type": "string"}, "type": "array"}}} |
| `batch_context_fingerprints` | object | no | `{}` | {"patternProperties": {"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$": {"type": "string"}}} |
| `storage_root` | string | no | `"packets"` | Project-relative root for packet storage. |
| `files` | map of string | yes | `required` | Packet logical file names mapped to paths. |
| `file_sha256` | map of string | no | `{}` | Evidence paths mapped to SHA-256. |
| `total_bytes` | integer | yes | `required` | {"minimum": 0} |
| `created_at` | string | no | `current UTC time` | UTC creation timestamp. |

### Model TaskEnvelope

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | 1 | no | `1` | Serialized record schema version; distinct from the plugin release version. |
| `task_id` | string | yes | `required` | {"pattern": "^task-[a-f0-9]{24}$"} |
| `stage` | enum ["source-review", "translate", "revise", "audit", "transcribe", "asset-audit", "scout", "terminology", "external-recheck"] | yes | `required` | Domain task/packet stage. |
| `role` | string | yes | `required` | Worker role or external-review role, according to record type. |
| `lens` | string or null | yes | `required` | Audit lens; required and validated for audit packets/runs. |
| `objective` | string or null | yes | `required` | Bound investigation objective, nullable for other stages. |
| `batch_ids` | array of string | yes | `required` | Ordered batch IDs covered by this record. |
| `pages` | string or null | yes | `required` | PDF page numbers or scoped page expression, as indicated by type. |
| `asset_ids` | array of string | yes | `required` | Original assets selected for a task. |
| `packet` | object | yes | `required` | Bound domain packet payload. |
| `domain_manifest` | string or null | yes | `required` | Project-relative workflow manifest path, when applicable. |
| `inputs` | map of string | yes | `required` | Bound project-relative input paths and hashes. |
| `context` | object | yes | `required` | Snapshot of shared context at task creation. |
| `instructions` | map of string | yes | `required` | Saved instruction paths and hashes. |
| `dispatch` | object | yes | `required` | Resolved host/role policy and dispatch information. |
| `source_sha256` | string | yes | `required` | Original PDF SHA-256. |
| `policy_snapshot` | object | yes | `required` | Saved policy or supplemental code annotations; see configuration reference. |
| `policy_domains` | object | yes | `required` | Saved policy or supplemental code annotations; see configuration reference. |
| `source_bindings` | map of string | no | `{}` | Selected unit fingerprints for task receipt validation. |
| `batch_binding` | object | no | `{}` | Snapshot of the translation batch manifest. |

### Model AssetCandidateInput

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `asset_id` | string | yes | `required` | Stable original-asset identifier. |
| `format` | enum ["latex", "table", "code", "text"] | no | `"latex"` | Structured candidate format; importer also checks source kind. |
| `content` | string or object | no | `""` | Candidate text or structured table object. |
| `status` | enum ["candidate", "unresolved"] | no | `"candidate"` | Stored state; importers control approval transitions. |
| `notes` | string | no | `""` | Explanatory notes; required by some domain conditions described above. |
| `semantic_uncertainty` | string | no | `""` | Unresolved interpretation concerns independent of representation quality. |

### Model AssetSubmission

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `packet_id` | string | yes | `required` | {"pattern": "^[a-f0-9]{64}$"} Identity of the saved domain packet. |
| `author_task_id` | string | yes | `required` | {"minLength": 1} |
| `model` | string or null | no | `null` | The packet's dispatch model, echoed verbatim: the value passed to the host's task launcher (a host alias or a concrete id). Not the served model. Absent only when the packet records no model and the task ran on the host's default. |
| `reasoning_effort` | string or null | no | `null` | The packet's dispatched reasoning effort, echoed verbatim. |
| `served_model_label` | string or null | no | `null` | The model the host environment reported to the task under the dispatch value, recorded verbatim and unverified; never used for gating. |
| `image_evidence` | map of string | yes | `required` | Viewed original image path to expected SHA-256. |
| `candidates` | array of [AssetCandidateInput](#model-assetcandidateinput) | yes | `required` | {"minItems": 1} |
| `usage` | object or null | no | `null` | Reported provider usage; absent usage is not estimated. |

### Model AssetReviewItem

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `asset_id` | string | yes | `required` | Stable original-asset identifier. |
| `candidate_sha256` | string | yes | `required` | Digest of the candidate under review. |
| `verdict` | enum ["accept", "reject", "unresolved"] | yes | `required` | Review verdict; valid enum and domain restrictions both apply. |
| `visual_checked` | boolean | yes | `required` | Attestation that original visual evidence was inspected. |
| `render_checked` | boolean | yes | `required` | Attestation that the actual candidate render was inspected. |
| `notes` | string | no | `""` | Explanatory notes; required by some domain conditions described above. |
| `semantic_uncertainty` | string | no | `""` | Unresolved interpretation concerns independent of representation quality. |

### Model AssetReviewSubmission

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `packet_id` | string | yes | `required` | {"pattern": "^[a-f0-9]{64}$"} Identity of the saved domain packet. |
| `reviewer_task_id` | string | yes | `required` | {"minLength": 1} |
| `image_evidence` | map of string | yes | `required` | Viewed original image path to expected SHA-256. |
| `render_manifest_sha256` | string | yes | `required` | {"pattern": "^[a-f0-9]{64}$"} Comparison dependency manifest digest. |
| `render_artifact_sha256` | string | yes | `required` | Rendered comparison artifact digest. |
| `decisions` | array of [AssetReviewItem](#model-assetreviewitem) | yes | `required` | {"minItems": 1} |

### Model PageVerificationReceipt

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | integer | no | `1` | Serialized record schema version; distinct from the plugin release version. |
| `page` | integer | yes | `required` | {"minimum": 1} |
| `source_sha256` | string | yes | `required` | Original PDF SHA-256. |
| `unit_fingerprint` | string | yes | `required` | Digest of the page's source units. |
| `asset_fingerprint` | string | yes | `required` | Digest of the page's original assets. |
| `validator_version` | string | yes | `required` | Version of the deterministic verifier used. |
| `receipt_key` | string | yes | `required` | Verification cache identity. |
| `passed` | boolean | yes | `required` | Result of the deterministic verification/QA checks. |
| `token_coverage` | number | yes | `required` | {"maximum": 1, "minimum": 0} |
| `errors` | array of object | no | `[]` | Blocking deterministic findings. |
| `checked_at` | string | no | `current UTC time` | UTC verification timestamp. |

### Model ExternalReviewRun

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | integer | no | `2` | Serialized record schema version; distinct from the plugin release version. |
| `run_id` | string | yes | `required` | Review-run identity. |
| `batch_id` | string | yes | `required` | {"pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"} |
| `reviewer_id` | string | yes | `required` | Configured external reviewer ID. |
| `driver` | [ExternalReviewDriver](#enum-externalreviewdriver) | yes | `required` | External CLI adapter. |
| `role` | string | yes | `required` | Worker role or external-review role, according to record type. |
| `requested_model` | string | yes | `required` | Dispatch model requested from the external CLI. |
| `actual_model` | string or null | no | `null` | Model identity established from CLI metadata, when available. |
| `actual_model_label` | string or null | no | `null` | Unnormalized CLI-reported model label. |
| `model_verified` | boolean | no | `false` | Whether CLI identity evidence matched the required model. |
| `cli_version` | string or null | no | `null` | Observed external CLI version. |
| `execution` | "cli" or null | no | `null` | External process execution marker; historical records may omit it. |
| `actual_effort` | string or null | no | `null` | Observed effort, when exposed by the provider metadata. |
| `effort` | string or null | no | `null` | Requested external CLI reasoning effort; subject to driver restrictions. |
| `fast_mode` | string or null | no | `null` | Observed fast-mode metadata. |
| `translation_fingerprint` | string | yes | `required` | Digest of the translation state checked. |
| `packet_sha256` | string | yes | `required` | Digest of the exact review packet. |
| `prompt_version` | string | yes | `required` | Version of the review prompt contract. |
| `scope` | [ReviewScope](#enum-reviewscope) | no | `"full"` | Full or incremental review coverage. |
| `base_run_id` | string or null | no | `null` | Prior run used as the incremental baseline. |
| `covered_unit_ids` | array of string | no | `[]` | Units covered by this external run. |
| `unit_fingerprints` | map of string | no | `{}` | Unit IDs mapped to review-input fingerprints. |
| `source_fingerprint` | string or null | no | `null` | Digest of selected source inputs. |
| `structure_fingerprint` | string or null | no | `null` | Digest of relevant source structure. |
| `context_fingerprint` | string or null | no | `null` | Digest of review context. |
| `duration_seconds` | number or null | no | `null` | Observed elapsed provider invocation time. |
| `usage` | [ReviewUsage](#model-reviewusage) or null | no | `null` | Reported provider usage; absent usage is not estimated. |
| `cost_usd` | number or null | no | `null` | Reported provider cost in USD; null when unavailable. |
| `prompt_delivery` | [PromptDelivery](#enum-promptdelivery) | no | `"file"` | How the external CLI receives its prompt. |
| `verdict` | [ExternalReviewVerdict](#enum-externalreviewverdict) | yes | `required` | Review verdict; valid enum and domain restrictions both apply. |
| `summary` | string | yes | `required` | Substantive summary of the external verdict. |
| `issue_ids` | array of string | no | `[]` | Canonical findings produced by this run. |
| `response_path` | string or null | no | `null` | Saved normalized response path. |
| `attempts` | integer | no | `1` | {"minimum": 1} |
| `failure_type` | enum ["authentication", "network", "format", "model", "quota", "timeout", "provider", "unknown"] or null | no | `null` | Classified invocation failure, nullable after success. |
| `fallback_of` | string or null | no | `null` | Primary/reviewer relationship of a fallback run. |
| `attempt_log_path` | string or null | no | `null` | Saved attempt telemetry path. |
| `success` | boolean | no | `true` | Whether invocation and response validation succeeded. |
| `reviewed_at` | string | no | `current UTC time` | UTC review timestamp. |

### Model ExternalReviewAttempt

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | integer | no | `1` | Serialized record schema version; distinct from the plugin release version. |
| `run_id` | string | yes | `required` | Review-run identity. |
| `batch_id` | string | yes | `required` | {"pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"} |
| `attempt` | integer | yes | `required` | {"minimum": 1} |
| `reviewer_id` | string | yes | `required` | Configured external reviewer ID. |
| `driver` | [ExternalReviewDriver](#enum-externalreviewdriver) | yes | `required` | External CLI adapter. |
| `requested_model` | string | yes | `required` | Dispatch model requested from the external CLI. |
| `actual_model` | string or null | no | `null` | Model identity established from CLI metadata, when available. |
| `effort` | string or null | no | `null` | Requested external CLI reasoning effort; subject to driver restrictions. |
| `prompt_delivery` | [PromptDelivery](#enum-promptdelivery) | yes | `required` | How the external CLI receives its prompt. |
| `duration_seconds` | number | yes | `required` | {"minimum": 0} |
| `success` | boolean | yes | `required` | Whether invocation and response validation succeeded. |
| `failure_type` | enum ["authentication", "network", "format", "model", "quota", "timeout", "provider", "unknown"] or null | no | `null` | Classified invocation failure, nullable after success. |
| `quota_pool` | enum ["cursor-first-party", "cursor-third-party"] or null | no | `null` | Optional historical provider quota classification. |
| `error` | string or null | no | `null` | Invocation error detail. |
| `targeted_repair_scheduled` | boolean | no | `false` | Whether response-format repair was scheduled. |
| `usage` | [ReviewUsage](#model-reviewusage) | no | `{"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0, "provider_turns": 0}` | Reported provider usage; absent usage is not estimated. |
| `cost_usd` | number or null | no | `null` | Reported provider cost in USD; null when unavailable. |
| `raw_response_path` | string | yes | `required` | Saved raw CLI response path. |
| `recorded_at` | string | no | `current UTC time` | UTC telemetry timestamp. |

### Model ReviewUsage

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `input_tokens` | integer | no | `0` | {"minimum": 0} |
| `cache_creation_input_tokens` | integer | no | `0` | {"minimum": 0} |
| `cache_read_input_tokens` | integer | no | `0` | {"minimum": 0} |
| `output_tokens` | integer | no | `0` | {"minimum": 0} |
| `provider_turns` | integer | no | `0` | {"minimum": 0} |

### Model AssetRef

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `kind` | string | yes | `required` | Asset representation kind. |
| `path` | string | yes | `required` | Referenced asset path. |
| `bbox` | tuple [number, number, number, number] | yes | `required` | PDF coordinates. |

### Model SourceFragment

| Field | Type | Required | Default | Constraints / meaning |
| --- | --- | --- | --- | --- |
| `page` | integer | yes | `required` | Minimum 1. |
| `bbox` | tuple [number, number, number, number] | yes | `required` | PDF coordinates. |

### Enumerated values

These are serialized strings, not Python enum member names. A shared record enum can include
values a particular command rejects; command-specific constraints still apply.

### Enum CalloutKind

`note`, `tip`, `warning`, `caution`, `whats-new`.

### Enum ExternalReviewDriver

`claude-code`, `antigravity`, `cursor-cli`, `codex-cli`, `opencode-cli`.

### Enum ExternalReviewVerdict

`accepted`, `changes-requested`, `inconclusive`.

### Enum IssueStatus

`open`, `resolved`, `rejected`, `waived`.

### Enum IssueType

`meaning`, `omission`, `addition`, `terminology`, `technical`, `style`, `reference`, `number-unit`, `format`.

### Enum ProjectStatus

`initialized`, `extracted`, `prepared`, `draft`, `qa-passed`, `reviewed`, `revised`, `machine-reviewed`, `external-reviewed`, `human-approved`.

### Enum PromptDelivery

`stdin`, `file`.

### Enum RenderPolicy

`include`, `omit`.

### Enum ReviewScope

`full`, `incremental`.

### Enum SemanticStatus

`unverified`, `auto`, `verified`.

### Enum Severity

`blocker`, `major`, `minor`, `suggestion`.

### Enum SidebarRole

`title`, `body`.

### Enum UnitKind

`heading`, `paragraph`, `list_item`, `note`, `code`, `equation`, `figure`, `caption`, `table`, `footnote`, `bibliography`.

## Configuration management

### littrans config show

See [configuration](configuration.md) for semantics and effects.

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory. |
| `--local/--no-local` | `boolean` | no | `false` | `[]` | Select machine bindings; cannot override project policy. |
| `--effective/--no-effective` | `boolean` | no | `false` | `[]` | Resolve host and role inheritance. |
| `--host` | `str` | no | `"auto"` | `[]` | Coordination host or auto. |

Example:

```text
littrans config show --help
```

### littrans config get

See [configuration](configuration.md) for semantics and effects.

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory. |
| `KEY` | `str` | yes | `required` | `[]` | Dot-separated configuration key. |
| `--local/--no-local` | `boolean` | no | `false` | `[]` | Select machine bindings; cannot override project policy. |
| `--effective/--no-effective` | `boolean` | no | `false` | `[]` | Resolve host and role inheritance. |
| `--host` | `str` | no | `"auto"` | `[]` | Coordination host or auto. |

Example:

```text
littrans config get --help
```

### littrans config schema

See [configuration](configuration.md) for semantics and effects.

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `--local/--no-local` | `boolean` | no | `false` | `[]` | Select machine bindings; cannot override project policy. |

Example:

```text
littrans config schema --help
```

### littrans config validate

See [configuration](configuration.md) for semantics and effects.

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory. |
| `--host` | `str` | no | `"auto"` | `[]` | Coordination host or auto. |

Example:

```text
littrans config validate --help
```

### littrans config set

See [configuration](configuration.md) for semantics and effects.

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory. |
| `KEY` | `str` | yes | `required` | `[]` | Dot-separated configuration key. |
| `VALUE` | `str` | yes | `required` | `[]` | String value unless --json is supplied. |
| `--json` | `boolean` | no | `false` | `[]` | Parse VALUE as JSON. |
| `--local/--no-local` | `boolean` | no | `false` | `[]` | Select machine bindings; cannot override project policy. |
| `--dry-run/--no-dry-run` | `boolean` | no | `false` | `[]` | Validate and show changes without writing. |

Example:

```text
littrans config set --help
```

### littrans config unset

See [configuration](configuration.md) for semantics and effects.

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory. |
| `KEY` | `str` | yes | `required` | `[]` | Dot-separated configuration key. |
| `--local/--no-local` | `boolean` | no | `false` | `[]` | Select machine bindings; cannot override project policy. |
| `--dry-run/--no-dry-run` | `boolean` | no | `false` | `[]` | Validate and show changes without writing. |

Example:

```text
littrans config unset --help
```

### littrans config apply

See [configuration](configuration.md) for semantics and effects.

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory. |
| `FILE` | `path` | yes | `required` | `[]` | Complete candidate YAML file. |
| `--local/--no-local` | `boolean` | no | `false` | `[]` | Select machine bindings; cannot override project policy. |
| `--dry-run/--no-dry-run` | `boolean` | no | `false` | `[]` | Validate and show changes without writing. |
| `--expect` | `str` | no | `null` | `[]` | Require this semantic SHA256 before writing. |

Example:

```text
littrans config apply --help
```

### littrans config reset

See [configuration](configuration.md) for semantics and effects.

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory. |
| `--preset` | `str` | yes | `required` | `[]` | Initialization preset name. |
| `--dry-run/--no-dry-run` | `boolean` | no | `false` | `[]` | Validate and show changes without writing. |

Example:

```text
littrans config reset --help
```

### littrans config presets

See [configuration](configuration.md) for semantics and effects.

No command-specific parameters.

Example:

```text
littrans config presets --help
```

### littrans context show

See [configuration](configuration.md) for semantics and effects.

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory. |
| `RESOURCE` | `str` | yes | `required` | `[]` | Fixed context resource name. |

Example:

```text
littrans context show --help
```

### littrans context validate

See [configuration](configuration.md) for semantics and effects.

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory. |
| `--resource` | `str` | no | `null` | `[]` | Fixed context resource name. |

Example:

```text
littrans context validate --help
```

### littrans context apply

See [configuration](configuration.md) for semantics and effects.

| Parameter | Type | Required | Default | Choices | Meaning |
| --- | --- | --- | --- | --- | --- |
| `PROJECT` | `path` | yes | `required` | `[]` | Project directory. |
| `MANIFEST` | `path` | yes | `required` | `[]` | YAML mapping resources to candidate files, plus a decision reason. |
| `--dry-run/--no-dry-run` | `boolean` | no | `false` | `[]` | Validate and show changes without writing. |
| `--expect` | `str` | no | `null` | `[]` | Require this semantic SHA256 before writing. |

Example:

```text
littrans context apply --help
```

## Compatibility aliases

| Deprecated entry | Canonical entry |
| --- | --- |
| `source prepare` | `source extract` |
| `batch create` | `translation batch create` |
| `batch show` | `translation batch show` |
| `batch refresh` | `translation batch refresh` |
| `qa run` | `translation qa` |
| `review import` | `translation review import` |
| `review import-set` | `translation review import-set` |
| `review resolve` | `translation review resolve` |
| `review issues` | `translation review issues` |
| `review status` | `translation review status` |
| `review external` | `translation review external` |
| `review external-migrate` | `translation review external-migrate` |
| `review external-adjudicate` | `translation review external-adjudicate` |
| `review external-status` | `translation review external-status` |
| `approve` | `translation approve` |
| `render` | `translation render` |
| `glossary lookup` | `context glossary lookup` |
| `glossary check` | `context glossary check` |

Deprecated aliases remain available throughout 0.8.x. Invocation emits a replacement hint to
stderr; stdout and exit-code contracts remain unchanged. Alias `--help` remains available, while
normal help lists canonical entries. Routing changes require no source re-extraction.
See [migration notes](cli-migration.md) for existing scripts and project documentation.
