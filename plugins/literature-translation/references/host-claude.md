# Claude Code
Install the plugin through its existing marketplace or --plugin-dir. Invoke a coordinator
skill and use the Agent tool with a thin literature-* agent. Definitions refer to the same
portable roles used by tasks; provide the task-bound role snapshot when executing a task.
Use the discovered namespaced type (for example
`literature-translation:literature-translator`), not an assumed unqualified name.
Each translation lens is a separate fresh agent. Do not preload coordinator skills into workers.
Pass the absolute start.md path and project root. Workers read the handoff's
`instructions/roles/` snapshot first, and resolve packet paths against that project root.
For direct execution without a handoff, Claude expands `${CLAUDE_PLUGIN_ROOT}` in the
agent body; it is not a shell environment variable. Do not search another installed version.

In non-interactive pilots, allow the coordinator's Agent, Skill and Read tools explicitly
and wait for foreground workers to finish. Read, Glob and Grep are sufficient for workers
returning content; use Read for page PNGs and instruction files. A tools allowlist on each
reviewer enforces its read-only capabilities; `readonly: true` alone is not a Claude control.
Preserve the exact worker response before import, including empty audit results. When a
writer cannot save, the coordinator saves its returned content; do not repeat successful work.
On Windows, preserve CLI stdout as UTF-8 bytes (for example, subprocess stdout to a binary
file). Shell pipelines can decode native output with a different console encoding and corrupt
Chinese before saving. If that happens, recover the exact result from Claude's native session
record and validate it before import; do not reconstruct translation from garbled text.

Project model aliases are dispatch values, not proof of the served model. Existing agent
definitions use effort: high; a configured per-dispatch effort remains advisory because the
adapter does not apply it. Read-only reviewers return content for the coordinator to save and
receive. Writers are limited by role/scope and domain validators.

External review always runs through `translation review external`, including when its CLI
provider is Claude Code. The adapter clears parent-session markers for the fresh read-only
process. Do not substitute a native Agent result. For a blind `external-recheck` task, use a
fresh namespaced `literature-technical-reviewer` Agent; the task stage overrides its ordinary
technical lens and JSONL response with the saved recheck JSON contract. Native discovery and model availability must be
verified in the user's configured client; deterministic tests do not certify a host pilot.
Official reference: https://code.claude.com/docs/en/sub-agents
