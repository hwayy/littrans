# OpenCode
Select --host opencode explicitly. The project adapter installs the four standard skills
under .opencode/skills and binds their references to project-local instruction resources.
Use LITTRANS_PLUGIN_ROOT for the project launcher when the installation location is custom.

Run project agents PROJECT --host opencode --check, then --write to create namespaced
.opencode/agents definitions with mode: subagent, skills and project-local instructions. Verify
discovery in a fresh session. The generated model is unset; configure provider/model through
the host's agent configuration. The adapter does not claim arbitrary model/effort selection
on a single Task call. Configured unsupported dispatch values produce advisories.

Dispatch independent native tasks within actual capacity. Read-only reviewers return content;
the coordinator persists/imports it. If native task or image capability is absent, use
fresh-session handoff or report the missing visual review, respectively.
Official references: https://opencode.ai/docs/agents/ and https://opencode.ai/docs/skills/
