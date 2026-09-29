# Translation Workflow

## Batches

Batches are cut when translation starts, at the user's request, never as the last step of source
preparation: the user may still correct verified source, and a batch freezes the units it was cut
from. `translation batch create PROJECT --pages PAGES` cuts verified pages into batches of about 900 source words
and a soft limit of 60 assets at logical boundaries; `--unit-ids` selects an explicit complete
unit set and `--untranslated-only` limits the editable scope to units without a current
translation. Both record `frozen_scope: true`. Refresh keeps their selected IDs rather than
filling interior gaps; removed IDs or newly cut logical groups require an explicit new selection.
Legacy/range manifests retain interval refresh behavior.

Untranslated-only manifests retain complete selected parent groups. Their `read_only_unit_ids`
are context, excluded from `translatable_unit_ids` and submission; refresh preserves that boundary
and reopens read-only units whose translations are missing or stale. Packet source and context
identify both scopes. Batch output schemas come from the submission model and include
`image_evidence` and `asset_translations`; refresh existing batches to update their emitted schema.

## Translation records

The [translation contract](cli-reference.md#translation-contract) defines submissions, image
companions and revision behavior. Translate only the editable scope; retain the packet's source
bindings and original-image evidence.

## Deterministic QA

`translation qa` checks the batch's editable units and its whole dependency closure. Its cached result is
bound to the translation fingerprint and a QA context that includes approved terminology, asset
semantic uncertainty, recorded translation uncertainties, dependency presence, dependency source
and translation hashes, each scoped translation's viewing receipt and the current required-image
hashes. Any change there makes the cached report stale; QA context versions change when a rule
changes, and existing reports must then rerun.

Rules worth knowing when reading a report:

- A source unit with prose outside its `{{asset:ID}}` placeholders needs target text; a target
  that consists only of placeholders is an `empty-translation`, while a source that is only an
  asset reference (a whole figure or table block) needs none.
- Real footnote calls are counted with multiplicity in prose and table cells; literal
  code/math/escaped syntax (including tilde and backtick fences and escaped dollar delimiters) is
  excluded. Live calls inside image-language companions are rejected.
- Dollar inline math requires non-whitespace inner boundaries and a closing dollar not followed
  by a digit and cannot span intervening unescaped dollars, so ordinary currency amounts retain
  their real footnote calls; explicit double-dollar display math remains protected. Code fences
  start on a line with at most three leading spaces and close only on an otherwise blank line
  with a matching or longer delimiter; unclosed blocks extend to end of input.
- Translatable dependency units, including read-only context, need current translations with
  current receipts (`missing-translation`, `source-hash-mismatch`, `asset-image-receipt-missing`).
- Asset uncertainty across the dependency scope, including non-translatable formulas, blocks
  approval (`asset-semantic-uncertainty`); damaged images remain routable to source repair.
- Structured target tables render once, including when `target_text` is explicitly empty.

### Terminology

See [glossary formats and matching](cli-reference.md#context-and-glossary). Use approved entries
for terminology that should gate QA, reference entries for contextual conventions, and candidates
for undecided proposals. Finish shared context changes before the audit wave.

## Audit coverage

Audit coverage is bound to the brief, the style guide, the relevant approved and reference
terms and the dependency-closure units of each run (`audit_context_text` is exactly those four
parts; `project.yaml` is not among them). `audit_coverage` (and `workflow status`,
`translation review status`) reports why a run no longer counts: `context-changed`, `dependency-changed`,
`unit-changed`, `invalidated`, `closure-incomplete` or `context-units-removed`. A
`context-changed` run recorded after this version also lists `context_changes` — which part
(`document-brief`, `style-guide`, `approved-terms`, `reference-terms`) changed and its line
count before and after — so the cost of growing a whole-file context is visible where it is
paid. Finish context edits before the audit wave.

`translation review import-set` rewrites reviewer issue ids to canonical `audit-<hash>` ids and keeps the
reviewer id as `source_issue_id`; `translation review resolve` accepts either. Review issues against
read-only context route to an editable owning batch; issues against non-translatable source units
dispatch source review.

## Workflow coordination

See [workflow packet interfaces](cli-reference.md#workflow-contract) for stages, selectors and manifests. All model work reads
original image evidence; a translate packet does not consume unverified transcription candidates.
New workflow packets record host/model/reasoning_effort in their manifest and identity, resolved from
the stage's own role in `agent_models.<host>`; each role carries its own model and effort. Either may be
absent, which dispatches on the host's default and is reported as an advisory, never refused. The
roles are `translate` (also `revise`), `transcribe`, `audit`, `asset-audit` and `source-review`; each
stage runs in a fresh subagent of its agent (see host-runtimes.md). Source-review material carries
its dispatch beside the packet, never inside it: `workflow packet --stage source-review`,
`source review-packets --host HOST` and the `source-review` task of `workflow next` report the
role's model and effort, while the source packet identity stays bound to content alone. Legacy
manifests remain readable with absent policy fields; create fresh packets for an explicitly bound
dispatch policy. `workflow status --host` uses the same override as next/packet, and
`project models PROJECT --host HOST` reports the resolved policy with its advisories.

Workflow coordination rechecks source authority across each selected batch's page-evidence
closure before reusing QA. Failed authority returns `source-review`, suppresses optional asset
tasks and permits `workflow packet --stage source-review` to rebuild review materials; the returned
materials include open `workflow_issues`, which must be addressed and resolved in their original
batches. Repair missing/changed source artifacts before independent review; cached translation
approval cannot override this gate.

A `revise` packet (`workflow packet --stage revise`) carries the translate packet files plus the
batch's current translation records, its open translation review issues and `<batch>.revise.md`. Revision
packets retain their originating batch IDs for coordinator issue resolution. A dependency-only
QA failure dispatches its editable owning batch as prerequisite work, even outside resume bounds;
`requested_batch_ids` retains the original selected wave in `workflow next`. Uncertain fallback
assets dispatch transcription before QA regardless of whether their owning source unit is
translatable, and workflow dispatches the independent asset audit before QA.

A batch set for a packet or a render may span batch series when units do not overlap and source
order holds; within one series the batches must stay consecutive.

`workflow next` and `workflow status` check batch coverage over the coordinated scope: the
pages of the coordinated batches (the bounded or series range for `next`, the requested IDs
for `status`) and the reading-order span between them. A renderable unit no manifest covers
inside that scope stops the wave (`unbatched_units=…`: refresh or create batches); pages
outside it that hold such units — a chapter extracted but not yet batched — are reported as
`unbatched_pages` and do not block coordinating the batched chapters. Formal rendering keeps
its own check over the pages it renders.
