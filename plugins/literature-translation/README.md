# Literature Translation 0.8.1

LitTrans coordinates source preservation, context management, translation and independent
review. The deterministic CLI stores evidence and resumes work; the host runs the models.
Source fidelity, translation approval and structured-asset reliability are separate states.

## Entry skills

| Skill | Responsibility |
| --- | --- |
| `literature-translation` | Coordinate the requested scope across stages and resume durable work |
| `source-processor` | Extract and review source, or transcribe and independently verify assets |
| `context-manager` | Maintain document rules, terminology proposals and context versions |
| `translation-coordinator` | Batch, translate, run QA and three audit lenses, revise and deliver |

Each skill works independently after checking its prerequisites. Workers follow the seven
portable role instructions in `roles/`; they do not load coordinator skills. Claude-compatible
agent definitions are thin adapters. Optional Codex/OpenCode project agents use the same roles.

## Runtime and CLI

Requires Python 3.12+. Run `python <plugin-root>/scripts/littrans.py doctor`. The managed
layout detector is installed with `layout install`; use only `layout install --repair` to
repair it. See [runtime](references/runtime.md), especially packaged Windows clients.

Canonical command groups are `project`, `source`, `assets`, `context`, `translation`, `task`,
`workflow`, `layout` and `doctor`. See [CLI migration](references/cli-migration.md) for compatible
old entries. Deprecated aliases remain throughout 0.8.x and warn on stderr; stdout and exit
codes retain their contract. Command reorganization does not require source re-extraction.

## Workflow

1. Initialize a project. Probe structure, dispatch bounded source investigation and apply
   supported rules. Run `source extract`, independently review original pages, and require
   `source verify`. Render the source checkpoint before creating translation batches.
2. Prepare document brief, style guide and glossary with Context Manager. Workers submit
   proposals; one coordinator merges accepted changes. Context snapshots explain changes;
   existing source/audit dependency checks remain authoritative.
3. Create batches with `littrans translation batch create PROJECT --pages PAGES` when
   translation is requested. Dispatch one translator per batch, receive
   the result and run `translation qa`. Original images are valid reading content.
4. Run three independent review tasks: fidelity, technical and chinese-style. Import empty
   results too. Revise open issues, verify the affected dependency closure and preserve
   configured external review requirements. Human approval always requires the user.
5. Use `translation render` for the requested scope. Draft previews use `--allow-draft` and
   remain drafts. Inspect actual reading artifacts before claiming visual verification.
6. Optional asset transcription and independent visual review may run later. Keep originals
   accessible and use them whenever a candidate remains uncertain or unverified.

## Portable tasks

`task create` wraps existing domain packets with role snapshots and input bindings. It does
not call a model. `task claim`, `task status`, `task receive` and `task release` manage execution
records; imported is not approved. See [task protocol](references/task-protocol.md).

Without native subagents, pass the generated `start.md` to a fresh session. Read-only reviewers
return content for the coordinator to save. The coordinator discovers saved results and
receives them through the same domain validators. Never count same-context self-review as
independent, or copied hashes as proof of image inspection.

## Host adaptation

See [host adapters](references/host-runtimes.md). Codex, Claude Code and OpenCode are targeted;
Cursor and Qoder retain general compatibility. Unknown/mixed environments use generic.
Use `--host opencode` explicitly. Actual native capacity and model availability govern dispatch.

Optional project-native configuration:

```text
littrans project agents PROJECT --host codex --check
littrans project agents PROJECT --host codex --write
littrans project agents PROJECT --host opencode --write
```

Use `--workspace REPOSITORY` for a containing repository. Generation preserves user edits.
Codex definitions omit fixed models; OpenCode 2.x definitions apply the project's model/effort
policy as `provider/model#variant` and install four project-local skills with bound references.
Regenerate OpenCode agents after policy changes. Empty policies preserve native inheritance.
Point `LITTRANS_PLUGIN_ROOT` to the installed plugin for custom paths.
Check native discovery in a fresh host session; generated files do not prove runtime support.

## Validation scope

Deterministic regression tests and host pilots are separate acceptance requirements.
OpenCode 2.0.6 has a synthetic translation/review pilot; it does not establish full-book or
complex-asset quality. Codex and Claude Code production pilots remain separate requirements.
See the repository [implementation contract](../../docs/v0.8-plan.md).
Keep private PDFs, translations, project state and model outputs outside the plugin repository.
