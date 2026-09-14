# PR #10 fifteenth review follow-up — 2026-09-13

All six findings on `cc3be70` were reproduced. Twelve regression paths failed on the
baseline: damaged candidate records (3), source table structure, orphaned translations,
same-import linked approvals (2 orders), literal footnotes (4), dependency asset recovery.

- Candidate hashes are checked before use; damaged candidates return blocking transcription
  recovery and new packet identities avoid cached responses tied to corrupt leaves.
- Asset lanes and packets use QA's dependency closure, including non-translatable formulas.
- Literal-aware footnote counters retain multiplicity; QA fingerprint is v6.7.
- Removed source units' current translations move to a separate retired ledger inside the
  authority transaction. An injected KeyboardInterrupt confirms both ledgers roll back.
- Source overrides publish before approvals; changed fingerprints defer the linked approval
  in either input order instead of publishing an immediately invalid receipt.
- Source HTML tables retain structured cells and resolve embedded assets within those cells.

Fifteen tests were added (fourteen in round15 plus one dependency parameter in round14),
including valid/multiplicity controls. Project schema stays 6; no model installation or
production PDFs were used. Full checks and exact-head CI are recorded below/in PR Validation.

Full `scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
831 passed / 2 skipped in 171.52 seconds, and doctor. Two existing tests require missing
local WPF/Bodenschatz PDFs. Full PR `git diff --check 38918b0` passed.
