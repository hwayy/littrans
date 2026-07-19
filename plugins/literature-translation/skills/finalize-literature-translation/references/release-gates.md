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

Draft rendering is for diagnostics only. A formal reading artifact must be rendered without `--allow-draft` and must include its quality status and private-use metadata.
