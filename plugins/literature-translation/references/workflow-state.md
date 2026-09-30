# Workflow State

## Independent states

- **Source fidelity**: all selected content has reviewed ownership, reading order and complete
  boundaries, bound to the source fingerprint.
- **Translation**: submitted prose, deterministic QA, three independent audit lenses, configured
  external review and optional explicit human approval.
- **Asset representation**: original image, structured candidate, independent visual/render
  review, reliable candidate or original-image fallback.

Translation and asset work advance independently after source fidelity. `optional_asset_tasks`
exposes enhancement work; `assets_complete` reports representation progress separately.
Saved policy can require reviewed inline/display math, structured tables without image fallback,
or completion of all applicable transcriptions. The scheduler promotes required asset work into
`ready_tasks`, and approval/final rendering enforce the requirement. See
[configuration](configuration.md) for the policy choices.

Missing understanding or source content always blocks: recorded translation `uncertainties`
block QA for the affected unit and dependencies. Merely unfinished transcription belongs to
asset progress, not an invented translation-understanding problem. Source/content/ownership
changes invalidate affected dependencies; presentation changes affect outputs and delivery
requirements without invalidating unrelated prose audits.

## Status snapshots

Each `workflow next` or `workflow status` invocation validates the source PDF once, then
shares its project configuration and policy digests across batch scheduling, QA context,
source fidelity, external-review chains, rechecks and dispatch advisories. A standalone
`translation review external-status` invocation uses the same operation-local reuse.
The number of full PDF hashes does not grow with the number of batches or reviewed pages.

Snapshots do not replace evidence checks: changed original images, stale packets, missing
receipts and changed review context still block the relevant stage. A new invocation reads
the saved policy and validates the source again. Translation-memory completion results are
reused only within the operation that owns the validated configuration.

Write boundaries remain independent. State saving validates the current files, and task
reception compares the freshly validated source identity with the task's recorded identity
before saving its result. External-review persistence rechecks current inputs after the
provider returns; it does not reuse the pre-call snapshot.

## Resume and recovery

Persist successful responses before parsing or import. A cached response is reusable only for the
same input, model, prompt and relevant output fingerprints. Recover serialization failures
offline; do not pay for an identical successful response again. Imports are idempotent, but
changed source or packet hashes require fresh evidence. Record actual host/model metadata and
measured usage; token or monetary costs unavailable from the host stay unknown.

After interruption, ask status for the frozen batch IDs, recover unimported successful responses,
then dispatch only missing stages. Preserve unresolved original-image assets after translation
rendering; this is a recoverable checkpoint, not a failed reading artifact.
