# Migrating to LitTrans 0.9.0-dev.3

This release requires project manifest v7 and settings schema v1. Unsupported versions are
rejected; there is no transparent upgrade. See [configuration](references/configuration.md)
for storage and policy semantics and the [CLI reference](references/cli-reference.md) for syntax.

Existing manifest-v7 projects with valid settings schema v1 can continue without rebuilding.
The operation-local source validation optimization changes no persisted schema or evidence
fingerprint. Use the rebuild procedure below when moving from an unsupported project format
or when deliberately starting a separate project.

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
