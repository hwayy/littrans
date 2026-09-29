# External CLI review and host recheck

External review is an optional project gate after passing deterministic QA, the three
internal audit lenses, issue resolution and machine approval. On every coordinating host,
run it through an external CLI process. Native host subagents never satisfy this gate.

## Configuration

Configure one fixed reviewer and an ordered, flat failure fallback chain in `project.yaml`:

```yaml
external_review:
  schema_version: 2
  enabled: true
  reviewer:
    id: primary
    driver: codex-cli
    command: codex
    model: YOUR_MODEL
    effort: high
  fallbacks:
    - id: backup
      driver: opencode-cli
      command: opencode
      model: PROVIDER/MODEL
      effort: high
  recheck:
    confidence_below: 0.9
    severities: [blocker, major]
```

Each entry independently supports `model`, optional `model_identity` and `effort`.
Choose models and supported effort levels from the local CLI/provider configuration; the
plugin supplies no external provider/model default. Omitted effort uses the model's CLI
default. Invalid or unsupported options must not be silently dropped.

| Driver | Model and effort mapping |
| --- | --- |
| `codex-cli` | `--model MODEL`, `-c model_reasoning_effort="EFFORT"` |
| `opencode-cli` | OpenCode 2.x: `--model provider/model#variant`; `effort` selects the variant |
| `claude-code` | `--model MODEL --effort EFFORT`; fast mode must remain off |
| `antigravity` | `--model MODEL --effort EFFORT`; some models do not accept effort |
| `cursor-cli` | Exact model IDs encode effort; omit separate `effort` and `fast` |

An embedded OpenCode `#variant` may replace `effort`; when both are supplied they must agree.
Custom variants are supported. `--thinking` displays thinking and does not select effort.
Use `model_identity` when a dispatch alias differs from the model reported by CLI metadata.
Identity verification does not accept model self-reports or requested arguments as evidence.
Requested and actual effort are separate; unavailable actual effort remains unknown.

Set optional `domain_expertise` for project-specific subject expertise. It is part of the
isolated packet and its fingerprint. Otherwise expertise follows the document brief.

## Run and migrate

Run `translation review external PROJECT BATCH`, or add `--dry-run` to inspect the packet,
commands, models, effort settings and fallback order without provider calls.
`translation review external-status PROJECT BATCH` reports authoritative gate state.

Every fresh external call starts with the fixed reviewer. Only invocation failures, including
missing commands, authentication, quota, timeout, invalid output or unverifiable model identity,
advance to the next fallback. Valid findings and inconclusive verdicts do not rotate providers.
No reviewer usage balancing, reservations or external second opinions are used.

For old projects, preview `translation review external-migrate PROJECT`, then run it with
`--apply`. It backs up `project.yaml`, selects the first old reviewer as primary, and flattens
each reviewer's model fallbacks before the next reviewer. Historical records remain intact.
Old host-subagent records and external second opinions are historical only; valid CLI primary
records may still count, subject to current fingerprints and recheck requirements.

Each external process receives only source, target, relevant images, checklist, style and
approved/relevant terminology. Existing findings and translator rationale are excluded.
Use fresh sessions and read-only execution. Keep normalized results, raw responses, CLI metadata,
version, fingerprints and attempt telemetry. Never convert external acceptance into human approval.

## Blind host recheck

An inconclusive content verdict, blocker/major finding, or finding below the confidence threshold
requires a fresh native host translation-reviewer subagent. Codex/OpenCode use the generated
`littrans-translation-reviewer`; Claude/Cursor/Qoder reuse `literature-technical-reviewer`
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

The recheck returns a JSON object with `verdict` (`accepted`, `changes-requested`, `inconclusive`),
`summary`, and `issues`. Each issue requires `unit_id`, `severity` (`blocker`, `major`, `minor`,
`suggestion`), `type` (`meaning`, `omission`, `addition`, `terminology`, `technical`, `style`,
`reference`, `number-unit`, `format`), exact `source_span`, exact `target_span`, `explanation`,
`suggested_revision` (empty string if none), and numeric `confidence` between 0 and 1.

The coordinator decision JSON includes `run_id`, `task_id`, `verdict`, an evidence-based
`reason`, and `issues`, mapping every external/recheck issue ID to an `action`
(`accept`, `reject`, `inconclusive`) and evidence-based `reason`. Agreement also requires a
recorded decision. Conflicts remain inconclusive until adjudicated by the coordinator.
Accepting a finding keeps it open until corrected; rejecting an evidenced false positive closes it.

Missing, stale or inconclusive rechecks/decisions block external approval. Translation changes
require fresh QA, internal audit and full/incremental external review. Resolving false positives
without changing evidence can satisfy the gate without another paid CLI call.
