# PR #10 eighth review follow-up — 2026-09-13

Baseline: `dc72bb6`. All six findings were reproduced independently.

## Changes

- Current dependency-only QA failures resolve to batches that can edit the failing
  units. Both next and status dispatch that work. Next preserves requested_batch_ids
  while allowing required owners outside resume bounds; wave limits still apply.
  Local errors remain locally editable; absent/cyclic ownership raises an actionable
  error rather than repeatedly dispatching an uneditable caller.
- QA includes translated table cells when checking footnote calls, and no longer
  lets image companions satisfy extracted-prose numbers, tokens or approved terms.
  QA context version 6.5 invalidates cached reports from the earlier policy.
- Status accepts the explicit coordination host through its API and CLI.
- Asset content identity version 2 binds kind, display and grouping semantics as well
  as original fragments and formula conditions. Final structure assembly rebinds any
  changes and unit hashes before publication. Legacy version 1 assets remain readable;
  re-preparation/semantic overrides require current evidence. Project schema stays 6.
- Untranslated-only batches retain selected logical groups. Explicit read-only unit
  IDs stay outside submission scope and survive refresh. Packets mark the editable
  IDs and include existing group translations as context without treating them as
  new approvals. Public schemas and the workflow contract are updated.

## Regression evidence

The first 11 cases produced 10 expected failures against the baseline after correcting
test fixture setup; the missing-table-call case already failed QA correctly.
The final 12 cases also cover a semantic source override invalidating a prior passing
  QA result, excluded read-only submission rejection, existing translation preservation,
bounded dependency dispatch, host CLI behavior, and progression after dependency repair.
The focused source/workflow suite passed 43 tests before the complete release check.

The full suite initially found six failures: forbidden-term checks must still cover
companions independently (restored), and two old tests allowed image labels to cover
omitted prose. Those tests now require QA rejection first and acceptance after correcting
the prose; visual label evidence remains available to reviewers.

No model install, production PDF, translation workspace or new visual approval is
included. `.claude/` remains untouched. Final validation and CI are recorded below
and in PR #10.

Final `scripts/check.ps1` passed release metadata/schema, Ruff, Mypy (34 source
files), **769 passed / 2 skipped in 128.05 seconds**, and doctor (`ok: true`).
The two skips are unavailable local WPF/Bodenschatz PDF fixtures. Whole-PR
`git diff --check 38918b0` passes. No new real model installation or inference ran.
