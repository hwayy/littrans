# Task protocol
The task envelope wraps an existing domain packet. Native packet schemas, validation and
approval gates remain authoritative. The envelope snapshots role instructions and reference
files; workers load only their role and the references it requests.

The envelope also saves `policy_snapshot` and consumed-domain digests. Workers follow that
snapshot and submit configuration proposals separately. Reception compares the relevant
current policy; dispatch-only changes preserve existing content evidence. See
[configuration](configuration.md#policy-and-evidence).

## Create and execute
See the [task interface and result contracts](cli-reference.md#task-contract) for stages,
selectors, command parameters and encodings. For blind external-review comparisons, see
[external review](external-review.md).

The returned handoff points to start.md beside task.json and its instruction snapshot.
Snapshots are stored and hashed as LF text, so Git's line-ending conversion of a committed
task does not invalidate it; any change to their content does.
Pass its absolute path to the worker. Paths inside the handoff are relative to its directory;
the project root is four parents above that directory, not necessarily the Git/workspace root.
Packet paths resolve from that project root. Saved role snapshots take precedence over installed
copies, so workers do not search another plugin version when running inside a nested project.
The coordinator claims the task with task claim PROJECT TASK_ID --executor ID --mode subagent
or fresh-session before dispatch. Give a worker the handoff, project location and necessary
images, without conversation history, other workers' candidates or expected verdicts.
Read-only native reviewers return content; the coordinator saves it as result.json.
Writers may save it themselves. The coordinator receives it with task receive PROJECT TASK_ID.
Source and asset reviews additionally require --confirm-visual-review after actual inspection.

Source-review workers may run `source preview-review` for assigned corrections, then return
the packet-bound `asset_crops` declaration in their native result. Preview does not import
the result or certify the new images; corrected pages require a fresh independent task.
Task results and inspection evidence are bound by original bytes. Scaffold protects the
task subtree with `-text -whitespace`; keep that rule after general text normalization rules.
Refresh existing scaffolds to add it. It cannot recover bytes already changed by a checkout:
restore those records from an intact copy, without replacing their recorded hashes.

## Recovery and authority
Task status reports pending/claimed/imported and result_available separately. A saved result
survives a failed import. Correct serialization offline, preserve prior responses and retry.
Imports retain the domain validators, source hashes and current context checks. Envelope
context checks are conservative: any changed context snapshot requires a new task, even where
older domain audit coverage may remain valid. Existing coverage still uses its finer rules.
An identical imported result is replayable; a different one needs a revision task.

Task completion does not establish QA success, source fidelity, audit approval or asset
reliability. Query domain status after import. Source corrections may require fresh tasks.
The task lock prevents conflicting claimed writers; source writers are conservatively
serialized, translators conflict by batch and transcribers by asset. It does not prevent
manual CLI writes or writers on another machine. Use the existing project record discipline.
Release an unfinished claim only after the named executor stopped.

A task ID or supplied image hash does not prove independent context, actual image viewing
or served model identity. Execution mode is declared provenance. Missing native subagents
uses the same handoff in a fresh session. A self-review in one context cannot substitute for
the three independent translation lenses. Missing vision leaves visual checks incomplete.
