# CLI migration in 0.8

Canonical commands group operations by domain. Skills coordinate tasks; commands execute
deterministic operations. Extraction alone does not establish source fidelity, and task
completion does not establish translation or asset approval.

See the complete [compatibility alias table](cli-reference.md#compatibility-aliases).

Arguments, defaults, JSON/JSONL stdout and exit codes remain compatible. Deprecated entries
print one replacement hint on stderr when executed and remain available throughout 0.8.x.
Their `--help` remains available; normal help lists canonical entries. The warning does not
turn a successful operation into a failure. Scripts must parse stdout separately from stderr.

`source render`, `assets`, `translation submit`, `workflow`, `project`, `layout` and `doctor`
retain their existing paths. The task interface reuses the domain validators;
it does not replace packet/import commands.

## Existing projects

No project schema or storage migration is needed for command routing. Do not re-extract
source or rewrite historical packets and receipts merely to replace command spelling.
Update user scripts at their next edit; existing calls remain valid. Refresh plugin-owned
project documentation with `project scaffold PROJECT --refresh`. User-owned handbooks and
launchers are preserved; update examples in them manually when needed.

The task protocol is independently versioned. Keep historical packets unchanged and create
a new task when current context or inputs no longer match. See task-protocol.md.
