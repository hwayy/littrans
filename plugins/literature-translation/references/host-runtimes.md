# Host adapters
Read only the adapter for the current host. The portable contract is [task protocol](task-protocol.md).
Codex, Claude Code and OpenCode receive targeted adaptation. Cursor/Qoder use the same role
instructions and domain gates with general compatibility; no separate workflow is maintained.

- [Codex](host-codex.md): ordinary subagent dispatch or optional project-native agents.
- [Claude Code](host-claude.md): plugin-native thin agent definitions.
- [OpenCode](host-opencode.md): project-native agents and discovered standard skills.
- [Generic, Cursor and Qoder](host-generic.md): available local subagents or fresh sessions.

Unknown/mixed environments use generic. Explicit --host wins. OpenCode currently requires
explicit selection rather than inference from an unverified environment variable.
A wave is a planning scope, never proof of available concurrent slots. Inspect actual tools.
Unavailable configured models are reported, never silently substituted. Model recommendations
live in profiles/host-models.yaml and project configuration, not worker instructions.
