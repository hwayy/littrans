# OpenCode

This adapter targets OpenCode 2.x (validated with 2.0.6). Select `--host opencode`
explicitly. Set `LITTRANS_PLUGIN_ROOT` for the project launcher when using a custom installation.

## Install and update project agents

Run `project models PROJECT --host opencode` to inspect `project.yaml` policies, then:

```text
project agents PROJECT --host opencode --check
project agents PROJECT --host opencode --write
```

Generation installs four skills, seven `littrans-*` agents and their instruction resources.
Use `--workspace REPOSITORY` when discovery should start at a containing repository.
Files stay portable: worker paths are anchored to the supplied task handoff or agent definition,
not the process working directory or Git root. Supply an absolute handoff path when dispatching.
Task snapshots are authoritative; do not substitute an installed role from another version.

The generator uses V2 `permissions:` rules. Read-only roles deny `edit` and `shell`, and all
workers deny further `subagent` calls. Do not add legacy `permission:` alongside these rules:
OpenCode 2.0.6 can discard a sibling `model:` when converting legacy agent configuration.
OpenCode 1.x is not supported by this generated format.

## Model policy

`project init` copies defaults from `profiles/host-models.yaml` into `agent_models.opencode`:

| Project role | Native agent | Default model / variant |
| --- | --- | --- |
| translate (also revise) | littrans-translator | openai/gpt-6-luna / max |
| transcribe | littrans-asset-transcriber | openai/gpt-6-luna / max |
| audit (all three lenses) | littrans-translation-reviewer | openai/gpt-6-sol / high |
| asset-audit | littrans-asset-reviewer | openai/gpt-6-sol / high |
| source-review | littrans-source-reviewer | openai/gpt-6-sol / high |

`project agents --write` renders each model and `reasoning_effort` as
`model: "provider/model#variant"`. Model-only policies omit the variant. An embedded variant
must agree with `reasoning_effort`; effort without a model is rejected before any files are
written. These are native agent settings, not model arguments on a single subagent call.
Scout and terminology roles have no model policy and inherit the parent model.

Existing projects keep their policy, including an empty `opencode: {}`. To adopt the new
defaults, copy the OpenCode section from the shipped profile into the project's `agent_models`.
To inherit instead, leave both model and effort unset. Rerun `--check` / `--write` after policy
changes and restart OpenCode. Check output includes the planned native model selectors.
User-edited managed files are preserved and reported as conflicts: back up and relocate those
files before regeneration, then reconcile intentional customizations explicitly.

Verify all roles with `opencode debug agents` from the configured directory in a fresh process,
and confirm actual child-session model/variant records. Higher-priority host configuration or
an unavailable provider/model must be reported, never silently replaced. Generated files alone
do not prove which model ran.

## Execute and resume

Use the native `subagent` tool with the `littrans-*` agent ID and the absolute task handoff.
Each audit lens needs a distinct child context. Read-only reviewers return content for the
coordinator to persist and import. Dispatch within actual host capacity.

An unset native child inherits the parent session's selected model and variant. A new top-level
`opencode run` session has its own selection: set `--model provider/model#variant` explicitly;
do not assume it inherits a previous main session or that `--agent` selects the model.
For fresh-session handoff use a primary-capable coordinator with the saved task instructions;
the generated `mode: subagent` definitions are intended for native child dispatch.

Prefer foreground child tasks for bounded CLI runs. If using background tasks, wait for their
actual terminal status or persist session IDs and resume explicitly. A parent CLI exiting is
not evidence that a child completed. Preserve results before `task receive`; failed imports
do not authorize restarting successful model work. Missing image capability leaves visual
review incomplete.

Official references: [agents](https://opencode.ai/v2/docs/agents),
[skills](https://opencode.ai/v2/docs/skills).
