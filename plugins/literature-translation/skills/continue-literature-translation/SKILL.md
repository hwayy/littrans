---
name: continue-literature-translation
description: Coordinate continuation of an existing littrans project in sets of at most three consecutive batches while preserving independent writers, three internal audit lenses, deterministic QA, machine approval, external review, and exact-set rendering. Use when asked to continue, resume, or efficiently advance a prepared technical-book, paper, article, or chapter translation across multiple batches.
---

# Continue Literature Translation

Coordinate state transitions; keep translation and review judgment independent. Use the bundled launcher in `../../references/runtime.md` for every `littrans` command. Follow [host-runtimes.md](../../references/host-runtimes.md) when assigning writers and reviewers. Keep all translation, audit, and external-review work on the local host; do not use cloud or remote subagents.

## Select work

1. Run `workflow next PROJECT --limit 3`.
2. Accept only its consecutive same-stage batch set. Finish that set before selecting another.
3. Use `workflow metrics PROJECT --batch-ids ID1,ID2,ID3` for a compact progress and usage snapshot. Do not load whole-project history into model context.
4. If the reported stage is `revise`, resolve, reject, or waive each open substantive issue, whether internal or external, revising the affected translation when required. Do not start another external review until `workflow next` advances past `revise`.

## Translate

1. Create one packet with `workflow packet PROJECT --stage translate --batch-ids IDS`.
2. Assign each batch to a different independent local writer. A writer may edit only that batch and must follow `translate-literature-section`. On Cursor, use the `literature-translator` agent.
3. Submit each result and run deterministic QA. A semantic no-op submission is success; do not manufacture a revision.
4. Do not let writers concurrently edit the glossary, source extraction, another batch, or shared evidence.

## Audit

1. Create three isolated packets for the same set, one each with `--stage audit --lens fidelity`, `technical`, and `chinese-style`.
2. Assign each lens to a different independent local reviewer. Give each reviewer its one packet and no prior findings. Each reviewer audits all selected batches in one pass. On Cursor, use `literature-fidelity-reviewer`, `literature-technical-reviewer`, and `literature-chinese-style-reviewer`.
3. Import each JSONL set with `review import-set PROJECT PACKET_MANIFEST ISSUES_JSONL`, including an empty file when no issue exists. On Cursor, persist each read-only reviewer's returned JSONL before importing; do not ask those reviewers to write the file.
4. After revisions, packet only the missing unit-level coverage. The CLI expands edits to continuation chains, complete sidebars/tables/figures, adjacent units, and batch seams.
5. Do not approve until all three current lens coverages are complete and every blocker/major issue is resolved, rejected with evidence, or explicitly waived.

## Approve and review externally

For every batch, run machine approval before external review. Keep each external service to one active call at a time; calls to two different services may run concurrently. Preserve the configured model, effort, second-opinion rule, and reviewer count.

Allow the CLI to choose full versus incremental external review. Incremental review is valid only when source and structure are unchanged, at most three translated units changed, the changed share is at most 20%, and the original reviewer remains available. Never override a forced full review.

## Render and finish

After external approval, render the exact set with `render PROJECT --batch-ids ID1,ID2,ID3 --name NAME`. Treat cross-batch seam QA as mandatory. Report the batch IDs, evidence coverage, unresolved issues, external verdicts, and output paths before starting the next set.
