# Migrating to LitTrans 0.9.1-dev.4

This release requires project manifest v7 and settings schema v1. Unsupported versions are
rejected; there is no transparent upgrade. See [configuration](references/configuration.md)
for storage and policy semantics and the [CLI reference](references/cli-reference.md) for syntax.

Existing manifest-v7 projects with valid settings schema v1 can continue without rebuilding.
This build changes no persisted schema and does not rewrite existing source evidence. Use the
rebuild procedure below when moving from an unsupported project format or when deliberately
starting a separate project.

Version 0.9.1-dev.4 adds exact reviewed `asset_crops` and `source preview-review` without
changing project schemas. Upgrade every host before using the new declarations; older
plugins cannot replay them. A crop changes evidence and requires a fresh independent review.
Existing source records are left intact until a supported correction or extraction command.
Re-extraction can merge assets that older unit overrides named separately. Such stale overrides
are rejected explicitly; refresh the correction against a new packet rather than discard it.
Refresh `project scaffold` to protect task results and inspection evidence from Git newline
conversion. Restore already-converted evidence from an intact copy; never adjust its hashes.

Applied composed operators such as `log log x`, and the operator names `erf` and `erfc`, no
longer count as translatable formula conditions. Existing original-image crops remain usable.
Remove any obsolete operator-only declarations through normal source-review overrides and
obtain fresh affected review receipts; do not change a receipt or declare an operator merely
to satisfy the language gate. Actual condition words and abbreviations remain subject to the
same declaration checks.

New tasks snapshot and hash their instructions as LF text, so Git's line-ending conversion no
longer invalidates them. Tasks created by earlier development builds still verify while their
snapshot bytes are unchanged; keep any `-text` attribute that protects them.

The layout worker's identity in layout fingerprints and the build digest are now computed from
LF text. Installations copied from a checkout that had written the plugin with CRLF therefore
get a new layout fingerprint once: the next extraction runs the detector again instead of
reusing the stored layout result. Clean LF installations are unaffected.

## Preserve the old project

Checkpoint workers, retain their successful responses and back up the entire project. Keep
its recorded plugin installation available. Do not point old and new clients at the same
project directory. Install the new plugin and start a fresh session; `littrans doctor` must
report the target build. Installation details are in [installation](references/installation.md).

## Rebuild into a new directory

Run `littrans project rebuild OLD NEW` with a nonexistent destination. The source must match
its recorded SHA-256. Rebuild reuses verified source and context material; historical task
results, source receipts, translations and approvals do not become evidence in the new project.
Review copied brief, style, glossary and structure guidance before using them. Historical model
answers must not enter blind review tasks. Keep `OLD` unchanged for recovery.

A v7 `OLD` keeps its validated `settings.yaml` (agents, translation and verification policy,
external reviewers) and the executable bindings of reviewers it defines. Add `--settings preset`
(optionally `--preset NAME`) to start from a preset instead. Older formats always start from a
preset. Read the returned `rebuild.configuration` report: `preserved`, `reset`, `local_migrated`,
`not_migrated` and `next_actions`. It is also saved in `derived/rebuild-provenance.json`.
`rebuild.unvalidated` lists copied `context/` and `glossary/` files that LitTrans neither
validates nor maintains, such as project-defined manifests or notes. They are copied unchanged;
whatever they record about the old project's extraction, packets or approvals may no longer
hold, so review, move or delete them before agents navigate by them.

## Configure the new project

Run `littrans config validate NEW --host HOST` and `littrans config show NEW --effective --host HOST`.
Policies are saved in `settings.yaml`; machine paths are in ignored `settings.local.yaml`.
The manifest identifies the source; `derived/project-state.json` stores workflow state.
Runtime operations do not inherit updated plugin presets.

Use `config set` for individual changes or `config apply` for a complete candidate with related
reviewer edits. Preview with `--dry-run`; use the current digest with `--expect` for concurrent
imports. Use `context apply` for shared content. Importing content does not approve it.
To move the source, update `source_path` with `config set NEW source_path PATH --local`;
the replacement must have the same hash. A different source document requires a new project.

Review resolved models, audit lenses, wave limits and external reviewer definitions. Local
executable bindings do not override project policy. Explicit unsupported host settings must be
cleared before dispatch. For OpenCode native agents, run `project agents NEW --host opencode
--check`, then `--write`; reconcile user-edited files and reload the host.

## Establish current evidence

Probe and extract source, create independent source-review tasks, receive their results and
require `source verify`. Inspect `source render` before batching and translation. Complete QA,
three independent audit lenses and configured external review/rechecks. Original assets remain
preserved; configured transcription and visual-evidence requirements can block final delivery.
Policy snapshots bind running tasks, so stale results must not be relabeled as current.

After configuration changes, follow the CLI's impact report and gate diagnostics. Re-run the
listed verification or reviews; no configuration command automatically rebatches, deletes
historical results or starts paid external calls. Confirm `config validate`, workflow status,
record tracking and rendered output before delivery.

## Recover

If rebuilding fails, keep the old project and retry in a fresh destination after correcting
the reported source or context problem. To resume old work, use `OLD` with its recorded plugin
build in a fresh session. Do not copy new approvals back into it. Restore a complete backup
when rolling back a modified project; preserve both records until recovery is verified.

Version history is maintained in [CHANGELOG](https://github.com/hwayy/littrans/blob/dev/0.9/CHANGELOG.md).
