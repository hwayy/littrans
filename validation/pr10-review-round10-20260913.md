# PR #10 tenth review follow-up — 2026-09-13

Baseline: `1c6e9b0`. The superseded asset-review replay finding was reproduced.

Immutable review replay now uses an absent-only index repair. It cannot replace a
different already-imported review for the same candidate. New review imports still
publish their decision normally, and replay still validates the original review and
render artifact before recovery. The operation remains under the project write lock.

Three baseline failures cover accept then reject, accept then unresolved, and reject
then accept, using different valid host-specific audit packets for the same candidate.
Each checks the unchanged current index, representation status and rendered candidate
visibility after replay. A fourth case verifies recovery when the index entry is absent.
Existing same-packet replay and corruption tests remain part of the full suite.

No schema change, model installation, production PDF or new browser rendering behavior
is introduced. Final full checks and CI are recorded below and in PR #10.

`scripts/check.ps1` passed: release metadata/schema, Ruff, Mypy (34 source files),
**779 passed / 2 skipped in 129.02 seconds**, and doctor. The two skips are the
unavailable local WPF/Bodenschatz PDF fixtures. Whole-PR whitespace checks pass.
