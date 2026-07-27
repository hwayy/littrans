# Release gates

`machine-reviewed` requires all of the following:

- complete translation coverage for the selected source units;
- passing current source verification, including visually verified LaTeX, structured tables, code layout, paragraph continuity, and figure labels;
- current source hashes;
- passing deterministic QA;
- completed fidelity, technical/terminology, and Chinese-style audit lenses;
- QA and audit translation fingerprints that match the current revision;
- no open blocker or major issue;
- a new audit after any revision that addressed a blocker or major issue.

`human-approved` additionally requires explicit user confirmation. Record it only through the guarded CLI flag.

When `external_review.enabled` is true, `external-reviewed` additionally requires:

- a current successful external run whose actual model is verified;
- an `accepted` external verdict for the current translation fingerprint;
- any required second opinion completed without a conflicting verdict;
- no open blocker, major, or minor issue (suggestions may remain).

Such projects require `external-reviewed` or `human-approved` for formal rendering and
translation memory. Projects without external review retain the legacy `machine-reviewed`
gate. External acceptance must never be recorded as human approval.

Draft rendering is for diagnostics only. A formal reading artifact must be rendered without `--allow-draft` and must include its quality status and private-use metadata.
