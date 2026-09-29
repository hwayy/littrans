# Migrating to LitTrans 0.8.3-dev.3

This guide targets the latest 0.8 build. Release history belongs in
[CHANGELOG](https://github.com/hwayy/littrans/blob/dev/0.8/CHANGELOG.md); command syntax is in the [CLI reference](references/cli-reference.md).

## Prepare and identify the project

Checkpoint workers and save successful responses. Back up the complete project record,
including receipt packets and provenance. Retain old plugin caches while tasks may use them.
Do not run different builds against the same project.

Read `schema_version` in `project.yaml`: schema 6 supports an in-place upgrade; older schemas
require a new directory. `derived/provenance.json` records the previous plugin build.
Install the target on every participating host using the [installation guide](references/installation.md),
start a fresh session, and run `littrans doctor` to confirm the version and build digest.

## Upgrade a schema-6 project in place

1. Run `littrans project scaffold PROJECT --refresh`, retaining any `--repo-root` selection.
   Scaffolding updates plugin-owned guidance and creates missing files; it preserves user-owned
   launchers and handbooks. Review old launchers against [runtime](references/runtime.md)
   before replacing them, keeping a backup.
2. Run `littrans project models PROJECT --host HOST`. Resolve advisories while preserving
   intentional model choices. For generated Codex or OpenCode agents, run `project agents
   PROJECT --host HOST --check`, then `--write` with the original `--workspace`. Back up
   conflicting user edits and reconcile them explicitly. Restart the host to load definitions.
3. If external review is configured, follow the next section before resuming it.
4. Run `littrans source verify PROJECT`, `littrans glossary check PROJECT`, and
   `littrans workflow status PROJECT --batch-ids BATCH_IDS`. Restore missing receipt packets
   or provenance from the backup. Re-run stale QA and create fresh independent review tasks
   for evidence the gates reject. Valid translations and approvals need no blanket reset.
5. Run `littrans project tracked PROJECT` and commit required record files. Re-render after
   changed source or review results. Use the four coordinator Skills for new work; workers
   use saved task instructions. See [task protocol](references/task-protocol.md).

Re-extraction is optional and bounded to pages needing correction. `source extract PROJECT
--replace --pages PAGES` preserves recorded layout and overrides. Use `--redetect` or
`--discard-overrides` only intentionally; review invalidated pages again. See
[source processing](references/source-processing.md) for correction and recovery details.

## Migrate external review when needed

Preview `littrans translation review external-migrate PROJECT`, then apply with `--apply`
after reviewing the changes. The command retains a byte-for-byte `project.yaml` backup and
converts legacy balancing to one reviewer with ordered fallbacks. Projects without external
review, or already using configuration v2, need no conversion.

External gates use provider CLI results. Historical native-host results and external second
opinions remain records but do not satisfy this gate. Required rechecks use blind host tasks
and explicit coordinator adjudication. Migration does not change provider accounts.
See [external review](references/external-review.md) for authentication and recovery.

## Rebuild an older project

Keep `OLD` intact and run `littrans project rebuild OLD NEW`, where `NEW` does not exist.
Rebuild reuses the source PDF, brief, style, glossary and documentation. It does not transfer
extractions, translations, verification receipts, batches or approvals as current evidence.

Reload the new project, confirm its source fingerprint and copied context, and inspect
`project models NEW --host HOST`. Probe and extract source, complete independent source review,
then require `source verify` and inspect `source render`. Create fresh batches and translation
tasks only for the requested scope. Structured asset transcription is optional in this branch;
original evidence remains the fallback. Historical answers must not enter blind reviews.

## Validate and recover

Resume from persisted workflow status and import saved successful responses before dispatching
missing work. Imports are idempotent; task completion alone is not approval. Inspect rendered
output and report pending work separately from delivered translation.

If upgrading fails, stop new workers, restore the complete backup and use its recorded plugin
build in a fresh session. For a rebuild, resume `OLD` with its old installation; do not copy
new evidence into it. Keep both directories until the new workflow is verified. Windows cache
relocation and layout setup are documented in [runtime](references/runtime.md).
