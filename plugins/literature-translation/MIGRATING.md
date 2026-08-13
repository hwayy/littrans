# Migrating LitTrans projects to schema v5

LitTrans 0.5.0 requires schema v5 before it records new workflow evidence. Finish active writer,
reviewer, and external-review processes, then commit or back up the private project.

Preview the lossless migration first:

```text
littrans project migrate PROJECT --to 5 --dry-run
```

Apply it, then start a new agent session:

```text
littrans project migrate PROJECT --to 5
```

The migration preserves source units, translations and revision history, issues and resolutions,
approval states, QA, internal audits, and external-review evidence. Schema-v4 evidence remains
readable until a relevant local source, translation, or context change invalidates it; migration
does not force a full-book re-review. Schema-v3 projects are upgraded through the existing v4
evidence migration and then to v5.

The v5 step creates `.littrans/work`, adds `/.littrans/` idempotently to the project `.gitignore`,
and records `evidence/migration-v4-v5.json`. It does not delete the legacy `packets/` directory.
The dry-run reports how many legacy packet directories are cleanup candidates. Inspect them with:

```text
littrans workflow prune-packets PROJECT --dry-run
```

Delete only candidates accepted by the CLI with `--apply`. The command never removes authoritative
translations, issues, audit evidence, external-review runs, or approvals.

After migration, select one wave once and use its compact status until it completes:

```text
littrans workflow next PROJECT --limit 3
littrans workflow status PROJECT --batch-ids ID1,ID2,ID3
```

Re-run only the missing batch-local QA, audit closure, or external review reported by status. Formal
rendering remains blocked until every selected batch has current source verification, deterministic
QA, all three audit lenses, machine approval, and any configured external approval.
