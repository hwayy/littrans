---
name: continue-literature-translation
description: Coordinate continuation of an existing littrans project in host-sized waves (Codex: 3 batches; Cursor: 6 by default, max 9) while preserving deterministic QA, three independent audit lenses, machine approval, external review, required second opinions, and single-batch rendering. Use when asked to continue, resume, or efficiently advance a prepared technical book, paper, article, or chapter translation across multiple batches.
---

# Continue Literature Translation

Use the launcher in `../../references/runtime.md` for every command and the local-agent rules in `../../references/host-runtimes.md`. Keep source material and model work on the local host.

## Freeze a wave

1. Establish the active resume boundary before selecting a wave. For a new project with no historic manifests, unbounded `workflow next PROJECT` is valid. For every resumed project that retains earlier manifests, recover the exact first in-scope batch ID from the user's durable checkpoint, the preceding wave handoff, or project-local continuation instructions, and run `workflow next PROJECT --start-at ID`. Add `--through ID` when the requested continuation has a fixed upper boundary. Never infer the boundary from the first globally incomplete manifest: historic batches can intentionally remain incomplete under the current translation ledger. If no durable boundary can be established, stop and ask the user instead of scanning from the beginning.
2. Run that bounded `workflow next` once to select one consecutive same-stage wave. The CLI auto-detects the coordinating host; pass `--host cursor` or `--host codex` to override. Codex defaults to `--limit 3` with a hard max of 3. Cursor defaults to `--limit 6` with a hard max of 9. Pass `--limit` only within that host's max, or when the user requests a different legal wave size.
3. Keep those batch IDs fixed until completion. Use `workflow status PROJECT --batch-ids IDS` thereafter; do not rescan the project with `workflow next`.
4. Give each writer or reviewer a fresh, minimal-context local task containing only its packet. Never pass prior findings, expected verdicts, or another agent's rationale.

If status is `revise`, handle only the affected batches. Consolidate all current issues for a batch into one revision pass, resolve or waive them with evidence, then run the closure requested by `workflow status`; do not block clean batches in the wave.

## Translate and audit

1. Create one translate packet per batch and run one independent writer per selected batch in parallel. Each writer edits only its assigned batch. Submit once and run deterministic QA. Do not idle a QA-passed batch because another batch in the wave is still translating.
2. Create audit packets with `workflow packet PROJECT --stage audit --lens all --batch-ids IDS`. Assign one lens reviewer across at most three consecutive batches. If the frozen wave is larger than three (Cursor), split it into consecutive groups of at most three so reviewer context stays bounded and more lens agents can run in parallel. Run fidelity, technical, and Chinese-style reviews independently; each lens receives only its compact packet.
3. Import every returned lens result, including an empty result. Consolidate all findings, then perform one combined revision pass per affected batch rather than one submission per issue. That combined pass covers the initial three-lens findings only; it is not a cap against a later closure revision.
4. Freeze the revised translation snapshot. Ask `workflow status` for the missing local closure and review only that unit/dependency closure once. Do not regenerate full-wave packets or empty evidence.
5. Accept remaining fluency, calque, or register defects that the closure lens identifies in the revised wording, including follow-on problems created by the first revision. Reject only duplicates, no-ops, seam-breaking moves, regressions of an accepted fix, and true preferences that do not name a defect. Do not reject a valid style finding solely to avoid another revision cycle, and do not treat typical-wave counts as a reason to leave a named fluency, calque, or register defect in place. If closure findings are accepted, make one more consolidated revision and then run only the CLI-reported missing coverage.
6. Machine-approve each batch only after deterministic QA and all three current lens coverages pass and every blocker/major issue is resolved, rejected with evidence, or explicitly waived.

## Review externally

Advance batches independently after machine approval. Different configured external services may run in parallel; allow at most one active call per service and rely on the CLI's cross-process service lock. Preserve the configured model, effort, reviewer count, and second-opinion rule.

Let the CLI choose full versus incremental review and the second-opinion dependency closure. On changes requested, consolidate that batch's external findings into one revision, run its local audit closure, and let the same reviewer perform the eligible incremental recheck. Never turn a failed provider attempt into a second opinion or override a forced full review.

When the coordinating host is Cursor, a `cursor-cli` review may use a local host subagent against the isolated dry-run packet. Retain the emitted `dry_run_path`; require the reviewer to echo the packet's `review_binding`, then record the result with paired `--from-result RESULT.json --from-dry-run DRY_RUN.json --actual-model "ACTUAL MODEL LABEL"` inputs. The trusted host coordinator obtains that label from Cursor task metadata; never use the reviewer model's self-report. If status requires a second opinion, create a new `--second-opinion --dry-run` with a different reviewer and import that result with the same protocol and that task's actual model label. Claude Code and Antigravity still use their CLIs.

## Finish

As soon as a batch independently passes its configured formal-render gate, render that batch with `--batch-id`. Omit `--name` to use the short batch key (the final `-bNNN` segment), writing `output/bNNN.*`. The renderer automatically includes the full cross-batch continuation/sidebar dependency chain. A formal render can therefore wait for a dependency batch's gate even when the selected batch itself is complete; never bypass that gate or accept a truncated logical structure. Later updates to the same batch overwrite those files. If another batch already owns that short name, the CLI refuses to overwrite it; rerun with an explicit unique `--name`.

```text
littrans render PROJECT --batch-id ID
```

The default short name is ownership-checked through its render-QA record. Do not wait for the rest of the wave.

`--batch-ids` combined rendering (one to nine consecutive batches) and `--pages` remain available for later collection or whole-book reading artifacts. They are not required in the regular translation wave.

Report batch IDs, current lens coverage, unresolved issues, external verdicts, and output paths. Record the first batch after the completed frozen wave as the next durable resume boundary, then select another wave with bounded `workflow next PROJECT --start-at ID`.
