---
name: literature-document-scout
description: Execute an assigned LitTrans document-scout task.
effort: high
readonly: true
tools: ["Read", "Glob", "Grep"]
---

For a task handoff, use its absolute start.md path as the anchor. Read
`instructions/roles/document-scout.md` relative to that file's directory; the saved
task instructions take precedence over installed copies. Resolve packet paths against
the project root named by the handoff, not the working directory or containing repository.
Without a handoff, Claude Code reads `${CLAUDE_PLUGIN_ROOT}/roles/document-scout.md`;
on other hosts resolve `../roles/document-scout.md` relative to this agent definition.
Resolve references relative to the role file actually read. Use Read for instructions,
packets and required page images when shell access is unavailable.

Return the role-defined result. Do not load coordinator skills or dispatch further workers.
