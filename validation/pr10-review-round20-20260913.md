# PR #10 twentieth review follow-up — 2026-09-13

All eight findings on `fc97f2f` were reproduced: the initial 29 regression cases failed.
The final 31 cases also cover mixed damaged/rejected candidate recovery and rollback
after a successfully published canvas is followed by an interrupted ledger write.

- QA v6.10 binds dependency content, source hashes and missing records into cached QA;
  untranslated-only batch creation keeps stale translations editable.
- Candidate replay repairs invalid/source-stale mappings. Review replay repairs invalid
  review mappings while preserving newer valid decisions (four existing replay controls).
- Page canvas encoding and publication are atomic, with original bytes restored if later
  source-authority publication fails. Existing overflow canvases are also snapshotted.
- Recovery packets contain blocking assets only and load revision context only for intact
  candidates. Image-language companions reject asset placeholders in text, cells and labels.
- Exact layout caches and worker results require list-valued predictions for every page.

The first full pytest run passed 894 tests and exposed one old workflow expectation:
changing a dependency closure now requires QA before audit. The test now verifies that
transition, submits the dependency, reruns QA and retains the original stale-audit assertion.
Project schema remains 6. No production PDFs or real model installation were used.

Final `scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
895 passed / 2 skipped in 164.46 seconds, and doctor. The two existing skips require local
WPF/Bodenschatz PDFs. Full PR `git diff --check 38918b0` passed; exact-head CI is linked
in PR Validation.
