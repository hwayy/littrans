# CLI migration in 0.8

Canonical commands group operations by domain. Skills coordinate tasks; commands execute
deterministic operations. Extraction alone does not establish source fidelity, and task
completion does not establish translation or asset approval.

| Deprecated entry | Canonical entry |
| --- | --- |
| `source prepare` | `source extract` |
| `batch create` | `translation batch create` |
| `batch show` | `translation batch show` |
| `batch refresh` | `translation batch refresh` |
| `qa run` | `translation qa` |
| `review import` | `translation review import` |
| `review import-set` | `translation review import-set` |
| `review resolve` | `translation review resolve` |
| `review issues` | `translation review issues` |
| `review status` | `translation review status` |
| `review external` | `translation review external` |
| `review external-status` | `translation review external-status` |
| `approve` | `translation approve` |
| `render` | `translation render` |
| `glossary lookup` | `context glossary lookup` |
| `glossary check` | `context glossary check` |

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
