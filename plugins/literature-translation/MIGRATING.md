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
littrans workflow next PROJECT
littrans workflow next PROJECT --host cursor
littrans workflow next PROJECT --host codex --limit 3
littrans workflow status PROJECT --batch-ids ID1,ID2,ID3
```

`workflow next` auto-detects the coordinating host. Codex remains default 3 / max 3. Cursor
defaults to 6 and allows up to 9. Status and packets accept up to nine consecutive batch IDs so
a Cursor wave can be coordinated as one set.

Re-run only the missing batch-local QA, audit closure, or external review reported by status. Formal
rendering remains blocked until that batch has current source verification, deterministic
QA, all three audit lenses, machine approval, and any configured external approval. Render each
ready batch with `render --batch-id ID`; do not wait for a combined `--batch-ids` artifact. The
default short output name is overwritten only for the same owning batch; use an explicit unique
`--name` when another batch already owns that suffix.

Cursor host-subagent external results now require the `dry_run_path` emitted by a fresh
`review external --dry-run`: pass it with `--from-result RESULT.json --from-dry-run DRY_RUN.json
--actual-model "ACTUAL MODEL LABEL"`. The trusted Cursor host coordinator must take the actual
model label from host task metadata; the reviewer must not self-report it. The reviewer result
must echo the packet's `review_binding`. Old or unbound dry-run records are intentionally rejected
and must be regenerated. Required second opinions use a separate `--second-opinion --dry-run` and
paired import with that task's actual model label.
