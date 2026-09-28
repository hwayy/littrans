# Workflow State

## Independent states

- **Source fidelity**: all selected content has reviewed ownership, reading order and complete
  boundaries, bound to the source fingerprint.
- **Translation**: submitted prose, deterministic QA, three independent audit lenses, configured
  external review and optional explicit human approval.
- **Asset representation**: original image, structured candidate, independent visual/render
  review, reliable candidate or original-image fallback.

The second and third states advance independently after source fidelity. Transcription is
optional enhancement and may be initiated at any later time, including after the reading edition
is complete. `workflow next` and `ready_tasks` schedule translation work; `optional_asset_tasks`
exposes the independent enhancement queue. `complete` means the reading workflow is complete,
while `assets_complete` separately reports enhancement progress.

Missing LaTeX never blocks an otherwise reviewed translation. Missing understanding or source
content does: nonempty recorded translation `uncertainties` block QA for the affected unit and its
semantic dependencies; resolve the actual understanding problem and resubmit before approval.
Merely unfinished LaTeX belongs to asset progress and must not be recorded as an unresolved
translation-understanding problem. Source/content/ownership changes invalidate affected translation
dependencies; a display-only change invalidates rendering evidence rather than unrelated prose audits.

## Resume and recovery

Persist successful responses before parsing or import. A cached response is reusable only for the
same input, model, prompt and relevant output fingerprints. Recover serialization failures
offline; do not pay for an identical successful response again. Imports are idempotent, but
changed source or packet hashes require fresh evidence. Record actual host/model metadata and
measured usage; token or monetary costs unavailable from the host stay unknown.

After interruption, ask status for the frozen batch IDs, recover unimported successful responses,
then dispatch only missing stages. Preserve unresolved original-image assets after translation
rendering; this is a recoverable checkpoint, not a failed reading artifact.
