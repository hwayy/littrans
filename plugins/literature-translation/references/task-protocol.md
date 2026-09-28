# Task protocol
The task envelope wraps an existing domain packet. Native packet schemas, validation and
approval gates remain authoritative. The envelope snapshots role instructions and reference
files; workers load only their role and the references it requests.

## Create and execute
Use task create PROJECT --stage STAGE with exactly one selector: --batch-ids, --pages or
--asset-ids. Translation/revision owns one batch. Audit requires one --lens (fidelity,
technical or chinese-style). Scout/terminology requires --pages and --objective.
Use an explicit --host on OpenCode; unknown or mixed environments resolve to generic.

The returned handoff points to start.md beside task.json and its instruction snapshot.
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

Result.json contains native JSON for source/asset tasks and JSONL for translate/revise/audit,
including an empty audit result. Scout results contain findings, proposed_rules and unresolved
lists; terminology results contain proposals and unresolved lists. Proposals are not approved.

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
