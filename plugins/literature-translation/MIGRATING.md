# Migrating LitTrans projects to schema v4

LitTrans 0.3.0 reads schema-v3 project content but requires a one-time migration before new v4
evidence is recorded. Finish any active writer or reviewer task and commit or back up the project
before migrating. Existing tasks may keep using their loaded older plugin; start a new Codex task
after the plugin and project have both been upgraded.

Preview the migration first. This command is read-only:

```text
littrans project migrate PROJECT --to 4 --dry-run
```

Then run the migration:

```text
littrans project migrate PROJECT --to 4
```

The migration preserves the PDF path and hash, source units, current translations, history and
revision numbers, issues, resolutions, and translation/project approval states. Schema v3 did not
bind QA to the approved glossary or internal audits to the document brief, style guide, and relevant
approved terms. QA is promoted only when it already carries the current glossary fingerprint;
legacy internal audits remain historical evidence and always require new three-lens coverage.
Accepted external runs are imported only when both their v3 translation fingerprint and exact legacy
review-packet hash still match. A changed brief, style guide, glossary, packet representation, or
translation therefore requires a new review.

Read these dry-run fields before proceeding:

- `batches`: every stored batch manifest, including unfinished ranges.
- `importable`: evidence that can be promoted without weakening the v4 gates.
- `pending_recheck`: batch IDs whose QA, three-lens audit, or external review must be rerun.

The write migration also performs one deterministic full-book source verification to seed page
receipts. No model is called and no translation, issue, revision, or approval status is rewritten.
Preserved approval text is not enough to pass a v4 gate when its current evidence is missing.

Evidence that is stale or cannot be tied to the current fingerprint is listed under
`pending_recheck` and is not promoted. After migration, use the coordinator to select the first
required stage and work in sets of at most three batches:

```text
littrans workflow next PROJECT --limit 3
littrans workflow metrics PROJECT --batch-ids ID1,ID2,ID3
```

Re-run only the indicated deterministic QA, audit coverage, or external review. Formal rendering
remains blocked until the selected batches have current source verification, QA, all three audit
lenses, machine approval, and any configured external approval. The full migration report is stored
at `evidence/migration-v3-v4.json`.

Migration is idempotent. Running it again on a v4 project reports no change.
