# External CLI review and host recheck

External review is an optional project gate after passing deterministic QA, the three
internal audit lenses, issue resolution and machine approval. On every coordinating host,
run it through an external CLI process. Native host subagents never satisfy this gate.

## Configuration

Set the fixed reviewer and ordered failure fallbacks using the
[external review contract](cli-reference.md#external-review-contract). Model identity must come
from CLI metadata, not model self-reports. Choose models available in your local provider setup.

## Run and migrate

Run `translation review external PROJECT BATCH`, or add `--dry-run` to inspect the packet,
commands, models, effort settings and fallback order without provider calls.
`translation review external-status PROJECT BATCH` reports authoritative gate state.

Every fresh external call starts with the fixed reviewer. Only invocation failures, including
missing commands, authentication, quota, timeout, invalid output or unverifiable model identity,
advance to the next fallback. Valid findings and inconclusive verdicts do not rotate providers.
No reviewer usage balancing, reservations or external second opinions are used.

Older project schemas must be rebuilt into a new v7 project. Historical approvals are not
inherited. Configure the new project using `config apply`; the legacy external-only migration
command cannot upgrade an older project manifest. Executable bindings belong in
`settings.local.yaml`; see [configuration](configuration.md).

Each external process receives only source, target, relevant images, checklist, style and
approved/relevant terminology. Existing findings and translator rationale are excluded.
Use fresh sessions and read-only execution. Keep normalized results, raw responses, CLI metadata,
version, fingerprints and attempt telemetry. Never convert external acceptance into human approval.

## Blind host recheck

An inconclusive content verdict, blocker/major finding, or finding below the confidence threshold
requires a fresh native host translation-reviewer subagent. Codex uses `littrans-translation-reviewer`; OpenCode uses the generated
`littrans-external-recheck`; Claude/Cursor/Qoder reuse `literature-technical-reviewer`
(with its discovered namespace). The external-recheck handoff overrides its ordinary lens
and output format; no new external-review agent is installed. Failed or unverified CLI calls
require CLI fallback/retry, not host recheck.

1. Create `task create PROJECT --stage external-recheck --batch-ids BATCH --host HOST`.
2. Claim with a new executor ID and `--mode subagent`, then dispatch the saved handoff.
3. Give the worker only task instructions and isolated evidence. It must not read external
   opinions, expected verdicts, or coordinator-side binding/receipt/decision records.
4. Persist its JSON response and run `task receive PROJECT TASK_ID --result FILE`.
5. Compare both opinions in the parent coordinator. Get the required issue IDs and result from
   `external-status`; submit `translation review external-adjudicate PROJECT BATCH FILE`.

Recheck triggered units and dependencies. Inconclusive verdicts without localized findings use
that external run's scope. Rechecks cannot modify translations, close findings, grant approval,
or count as any of the three ordinary audit lenses.

Result fields and the coordinator decision object are defined in the
[external review contract](cli-reference.md#external-review-contract).

Missing, stale or inconclusive rechecks/decisions block external approval. Translation changes
require fresh QA, internal audit and full/incremental external review. Resolving false positives
without changing evidence can satisfy the gate without another paid CLI call.
