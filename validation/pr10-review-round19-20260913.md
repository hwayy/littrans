# PR #10 nineteenth review follow-up — 2026-09-13

All four findings on `480f22f` were reproduced; the initial nine regression cases failed.

- QA v6.9 rejects missing/stale translations throughout the translatable dependency scope,
  including read-only context. Workflow tests verify dispatch to the editable owning batch.
- Batch refresh retains read-only translated units only while their source hash is current;
  missing/stale records reopen the unit for submission. Non-translatable context stays read-only.
- Source/QA and Markdown share a tokenizer that excludes escaped opening/closing dollars;
  HTML also ignores escaped openings. Currency preceding math no longer swallows real calls.
- Layout cache scans require the recorded image key and list-valued predictions, continuing
  past partial matching entries to find valid evidence.

Eleven new cases cover these changes, including stale dependency and escaped closing-dollar
controls. Project schema remains 6; no production PDF or model install. Full checks and
exact-head CI are recorded below/in PR Validation.

Full `scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
864 passed / 2 skipped in 161.90 seconds, and doctor. The two existing skips require local
WPF/Bodenschatz PDFs. The first full run exposed an old seam fixture expecting QA before
its dependency was translated; it now submits both fragments before asserting QA, preserving
the original local-scope assertions. Full PR `git diff --check 38918b0` passed.
