# Codex
Use the four coordinator skills. Spawn a fresh subagent with the assigned task handoff and
the project's available model/effort. Do not fork a context containing writer rationale,
parallel candidates or expected verdicts. On hosts without native delegation, use fresh-session
handoff. Do not substitute sidebar chats for subagents without user authorization.

Inspect the available spawn tool before dispatch. Where `fork_turns` is supported, set
`fork_turns="none"` explicitly: its default may inherit the whole conversation and reject
model/effort overrides. Pass the task envelope's `dispatch.model` and
`dispatch.reasoning_effort` only when set and supported by the host. Do not silently
substitute a different model if dispatch fails. Omitted settings follow host defaults.
Give each audit lens a fresh worker; never reuse a writer or another lens's context.
Queue work within the host's currently available slots and collect completed results.
Read-only reviewers return native result content for the coordinator to save and receive.
If a writer cannot save under the active filesystem permissions, retain its exact native
response and let the coordinator persist it through the host's normal approval mechanism.
Do not rerun successful translation solely because result persistence was denied.

Optional: project agents PROJECT --host codex --check; then --write to generate namespaced
.codex/agents/littrans-*.toml and project-local instruction resources. Use --workspace for
a containing repository. This never edits global configuration or registers agents merely
by shipping them in a plugin. User-edited generated files cause conflicts, not overwrites.
Restart the host session as needed and verify native discovery. Generated definitions omit
model/effort so per-dispatch/project policy remains available. Sandbox settings are best-effort
host controls; runtime permissions still govern. Generated roles set `agents.enabled=false`
and forbid nested delegation in their instructions. Some host builds still expose delegation
tools to such workers; verify actual availability and do not treat this setting as a security boundary.
With a handoff, load its role snapshot and resolve packet paths from its project root.
Without one, resolve the copied role relative to the agent definition, not the shell directory.

Windows runtime repairs remain exclusively layout install --repair; see runtime.md for
AppData redirection. Preserve caches used by running tasks.
Official reference: https://learn.chatgpt.com/docs/agent-configuration/subagents
