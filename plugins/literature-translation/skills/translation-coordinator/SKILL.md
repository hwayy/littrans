---
name: translation-coordinator
description: Coordinate LitTrans batching, isolated translation and revision, QA, three independent audit lenses, configured external review and reading-edition delivery.
---

# Translation Coordinator

Validate effective project configuration for the selected host before creating tasks.
Use `config` CLI for accepted policy changes; inspect affected tasks and evidence.
Dispatch each stage and audit lens with its resolved role policy. A task's saved
policy snapshot controls its work. When receive reports stale policy, retain the
result and create a fresh task. See [configuration](../../references/configuration.md).

You coordinate; fresh workers write translations and audit them.

Read [task protocol](../../references/task-protocol.md), [translation workflow](../../references/translation-workflow.md), and [release gates](../../references/release-gates.md).

1. Verify requested source and context readiness. Use `translation batch create` only when translation is requested. Preserve semantic parent groups, source order and cross-page dependencies; use logical boundaries around the existing word/asset budget.
2. Query workflow next/status for fixed batch IDs. Create one translate task per batch. Queue within actual host capacity; wave size is not concurrent-agent capacity. Optional transcription has a separate queue.
3. Receive translations and run translation qa. Correct deterministic errors through the translator. Then create three fresh audit tasks, one each for fidelity, technical and chinese-style. Reviewers do not see expected verdicts or each other's findings. Import empty results too.
4. Consolidate open issues in a revise task. The writer reports addressed IDs; the coordinator resolves issues only with supporting revision evidence. Run missing dependency-closure audits until current coverage and blocker/major gates pass.
5. Run configured [external review](../../references/external-review.md) through external CLIs on every host. Dispatch required blind, targeted host rechecks in fresh subagents; compare both opinions and record an evidence-based coordinator adjudication. Approve only through translation approve at the actual level achieved. Never infer human approval.
6. Render requested batches with translation render. Use --allow-draft only for a clearly identified preview. Inspect originals, offline rendering, captions, footnotes, equation numbers and narrow layouts; report artifact paths and any unverified visual checks.

Translation completion and asset completion remain separate. Save successful results before import and resume missing tasks, not the whole workflow.
