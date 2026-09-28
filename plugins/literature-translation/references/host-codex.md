# Codex
Use the four coordinator skills. Spawn a fresh subagent with the assigned task handoff and
the project's available model/effort. Do not fork a context containing writer rationale,
parallel candidates or expected verdicts. On hosts without native delegation, use fresh-session
handoff. Do not substitute sidebar chats for subagents without user authorization.

Optional: project agents PROJECT --host codex --check; then --write to generate namespaced
.codex/agents/littrans-*.toml and project-local instruction resources. Use --workspace for
a containing repository. This never edits global configuration or registers agents merely
by shipping them in a plugin. User-edited generated files cause conflicts, not overwrites.
Restart the host session as needed and verify native discovery. Generated definitions omit
model/effort so per-dispatch/project policy remains available. Sandbox settings are best-effort
host controls; runtime permissions still govern.

Windows runtime repairs remain exclusively layout install --repair; see runtime.md for
AppData redirection. Preserve caches used by running tasks.
Official reference: https://learn.chatgpt.com/docs/agent-configuration/subagents
