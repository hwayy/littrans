---
name: continue-literature-translation
description: Coordinate continuation of an existing littrans project in fixed waves of at most three consecutive batches while preserving deterministic QA, three independent audit lenses, machine approval, external review, required second opinions, and exact-set rendering. Use when asked to continue, resume, or efficiently advance a prepared technical book, paper, article, or chapter translation across multiple batches.
---

# Continue Literature Translation

Use the launcher in `../../references/runtime.md` for every command and the local-agent rules in `../../references/host-runtimes.md`. Keep source material and model work on the local host.

## Freeze a wave

1. Run `workflow next PROJECT --limit 3` once to select one consecutive same-stage wave.
2. Keep those batch IDs fixed until completion. Use `workflow status PROJECT --batch-ids IDS` thereafter; do not rescan the project with `workflow next`.
3. Give each writer or reviewer a fresh, minimal-context local task containing only its packet. Never pass prior findings, expected verdicts, or another agent's rationale.

If status is `revise`, handle only the affected batches. Consolidate all current issues for a batch into one revision pass, resolve or waive them with evidence, then run the closure requested by `workflow status`; do not block clean batches in the wave.

## Translate and audit

1. Create one translate packet per batch and run up to three independent writers in parallel. Each writer edits only its assigned batch. Submit once and run deterministic QA.
2. Create the initial audit with `workflow packet PROJECT --stage audit --lens all --batch-ids IDS`. Run fidelity, technical, and Chinese-style reviews independently and in parallel; each lens receives only its compact packet.
3. Import every returned lens result, including an empty result. Consolidate all findings, then perform at most one combined revision pass per affected batch.
4. Freeze the revised translation snapshot. Ask `workflow status` for the missing local closure and review only that unit/dependency closure once. Do not regenerate full-wave packets or empty evidence.
5. Machine-approve each batch only after deterministic QA and all three current lens coverages pass and every blocker/major issue is resolved, rejected with evidence, or explicitly waived.

## Review externally

Advance batches independently after machine approval. Different configured external services may run in parallel; allow at most one active call per service and rely on the CLI's cross-process service lock. Preserve the configured model, effort, reviewer count, and second-opinion rule.

Let the CLI choose full versus incremental review and the second-opinion dependency closure. On changes requested, consolidate that batch's external findings into one revision, run its local audit closure, and let the same reviewer perform the eligible incremental recheck. Never turn a failed provider attempt into a second opinion or override a forced full review.

## Finish

Render the fixed wave only after every selected batch passes its configured gate:

```text
littrans render PROJECT --batch-ids ID1,ID2,ID3 --name NAME
```

Report batch IDs, current lens coverage, unresolved issues, external verdicts, and output paths. Only then select another wave with `workflow next`.
