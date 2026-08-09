# Migrating LitTrans projects to schema v4

LitTrans 0.3.0 reads schema-v3 project content but requires a one-time migration before new v4
evidence is recorded. Preview it first:

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
approved terms, so those records are preserved as history but always listed for recheck. Accepted
external runs are imported only when their v3 fingerprint still matches the current batch. The
migration also performs one deterministic full-book source verification to seed page receipts. No
model is called and no translation is rewritten.

Evidence that is stale or cannot be tied to the current fingerprint is listed under
`pending_recheck` and is not promoted. Re-run only the indicated deterministic QA, audit coverage,
or external review. The full migration report is stored at `evidence/migration-v3-v4.json`.

Migration is idempotent. Running it again on a v4 project reports no change.
