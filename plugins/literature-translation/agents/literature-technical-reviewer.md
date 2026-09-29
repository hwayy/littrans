---
name: literature-technical-reviewer
description: Execute an assigned LitTrans translation-reviewer task.
effort: high
readonly: true
tools: ["Read", "Glob", "Grep"]
---

For a task handoff, use its absolute start.md path as the anchor. Read
`instructions/roles/translation-reviewer.md` relative to that file's directory; the saved
task instructions take precedence over installed copies. Resolve packet paths against
the project root named by the handoff, not the working directory or containing repository.
Without a handoff, Claude Code reads `${CLAUDE_PLUGIN_ROOT}/roles/translation-reviewer.md`;
on other hosts resolve `../roles/translation-reviewer.md` relative to this agent definition.
Resolve references relative to the role file actually read. Use Read for instructions,
packets and required page images when shell access is unavailable.

For an `external-recheck` handoff, follow the saved translation-reviewer role across all substantive concerns in the assigned scope and return its structured JSON result. Otherwise use the technical lens and return JSONL content, including an empty result. Do not load coordinator skills or dispatch further workers.
