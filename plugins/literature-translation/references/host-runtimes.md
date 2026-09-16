# Host runtimes

LitTrans supports local Codex, Cursor, Claude Code and Qoder orchestration through the launcher in [runtime.md](runtime.md). Keep source PDFs, project state and review workspaces on the local host. External reviewer services remain controlled by project configuration.

## Scheduling and models

| Host | Batch wave default | Batch wave maximum | Translation and transcription |
| --- | ---: | ---: | --- |
| Codex | 3 | 3 | `agent_models.codex` (recommended: fresh `gpt-5.6-luna`, `max` effort) |
| Cursor | 6 | 9 | `agent_models.cursor`: explicit host-available role model configuration |
| Claude Code | 3 | 6 | `agent_models.claude` (recommended: `sonnet`, `high` effort) |
| Qoder | 3 | 6 | `agent_models.qoder`: explicit host-available role model configuration |

A wave is a coordination scope, not permission to exceed the host's active task capacity. Transcription is optional and can run at any later time. When selected, independent transcribe and translate tasks receive the same source text and original images with no shared candidate output. Queue work when slots are full; a finished translation may enter its audit while other work continues.

`workflow next` auto-detects the host from its environment (`CODEX_*`, `CURSOR_*`, `CLAUDECODE`, `QODER_*`); mixed or unknown environments use Codex. Explicit `--host codex`, `--host cursor`, `--host claude` or `--host qoder` overrides detection. Pass the same explicit `--host` to `workflow packet` (including transcribe and asset-audit) and direct `assets packet`; it overrides local detection during packet validation. Once selected, keep batch IDs fixed and use `workflow status --batch-ids`. Assign a translation audit lens across at most three consecutive batches; split larger Cursor waves accordingly.

Role models are not hard-coded. The plugin ships recommended defaults per host in `profiles/host-models.yaml`; `project init` copies them into the project's `project.yaml` under `agent_models`, where each project should confirm or override them. Every configured value is a **dispatch value**: what the coordinator hands to the host's task launcher, a category alias (`sonnet` on Claude Code) or a concrete id as the host's convention has it. Which model the host serves under that value is the host's own configuration; the plugin never verifies a dispatched subagent's model. Packets carry the configured model and effort, and submissions echo them (`assets submit` rejects a candidate whose `model`/`reasoning_effort` differ from its packet); a writer may additionally record what its environment reports in `served_model_label`, which is stored verbatim, unverified and never gated. When the required model is unavailable, report the configuration problem; do not silently substitute. Existing review-role model configurations remain in force. Fresh task context is required for independence; a task fork that contains another worker's candidate or expected verdict is not independent.

## Codex

Invoke `$skill-name`; UI metadata lives in each skill's `agents/openai.yaml`. Use the local host's independent subagent mechanism for the two production lanes and separate reviews. Provide only the assigned packet and necessary original images. Start a new task after changing the installed plugin version; do not remove caches used by a running task.

## Cursor

Invoke `/skill-name` or its natural-language name. Plugin agents provide `literature-translator`, `literature-transcriber`, `literature-asset-reviewer`, the three translation audit lenses and `literature-external-reviewer`. Launch fresh local Task subagents; do not use Cursor Cloud Agents or `/in-cloud`.

Reviewers are read-only. Translation lenses return JSONL issues (an empty result still needs import). Asset reviewers return the packet's bound review JSON. The parent persists and imports those responses. Translators and transcribers write only their assigned output and submit through the CLI. Reload the window and start a new session after a plugin update. On every host a plugin update is only picked up when the plugin version string changes; a development build with an unchanged version must be reinstalled (or loaded from the checkout with `--plugin-dir`).

## Claude Code

Install through the repository marketplace (`.claude-plugin/marketplace.json`) or load the plugin tree with `claude --plugin-dir`. Skills are invoked as `/literature-translation:skill-name` (or by describing the task); the same `agents/*.md` files provide the plugin subagents, addressed as `literature-translation:agent-name` through the Agent tool.

The coordinating session runs the CLI itself and dispatches one fresh subagent per packet. Pass the packet path, the project path and the packet's `model` (the Agent tool's `model` parameter, e.g. `sonnet`); the writer agents declare `effort: high` in their frontmatter, matching the recommended `agent_models.claude.reasoning_effort`. The alias is resolved by the host's provider configuration and may land on any model; a transcriber echoes `sonnet` in its submission and, if it knows what was served, records that in `served_model_label`. Reviewer agents are restricted to `Read`, `Glob` and `Grep`, so they cannot write project files: the parent saves and imports their JSONL issues or review JSON. Translators and transcribers run `translation submit`, `qa run` or `assets submit` themselves and report the outcome. Independence rules are unchanged: give a subagent only its packet, never another worker's candidate or an expected verdict.

For a `revise` stage, create a `--stage revise` packet and dispatch a fresh `literature-translator` with the translate model; it resubmits the batch and reports the issue ids it addressed, which the parent closes with `review resolve` (canonical or reviewer-supplied ids, comma-separated). The CLI reconfigures stdout and stderr to UTF-8 with LF line endings, so piping its JSON into files on a GBK Windows console needs no `PYTHONIOENCODING` and produces no carriage returns.

Permission prompts apply to the CLI; allowing `Bash(python <plugin-root>/scripts/littrans.py *)` avoids repeated approvals. The `claude-code` external-review driver must not be launched from inside a Claude Code session (nested `claude -p`); Claude-hosted external review is a separate, later revision.

## Qoder

Qoder is Claude-Code-compatible: it reads the same plugin tree through `.qoder-plugin/plugin.json` and reuses the shared `skills/*/SKILL.md` and `agents/*.md`. Install through the repository marketplace (`.qoder-plugin/marketplace.json`) or copy the plugin directory into `~/.qoder-cn/plugins/literature-translation`, enable it, and start a new session. Skills are invoked as `/literature-translation:skill-name` (or by describing the task); the same `agents/*.md` provide the plugin subagents, addressed as `literature-translation:agent-name`.

The coordinating session runs the CLI itself and dispatches one fresh subagent per packet. Pass the packet path, the project path and the packet's `model`. Because Qoder's accepted model aliases are not bundled, `agent_models.qoder` ships empty: configure the dispatch values per project before creating translate, revise or transcribe packets, or the CLI reports the missing `agent_models.qoder` configuration. Reviewer agents are restricted to `Read`, `Glob` and `Grep`, so they cannot write project files: the parent saves and imports their JSONL issues or review JSON. Translators and transcribers run `translation submit`, `qa run` or `assets submit` themselves and report the outcome. Independence rules are unchanged: give a subagent only its packet, never another worker's candidate or an expected verdict.

As on every host, a plugin update is only picked up when the version string changes; recopy the plugin directory and start a new session after a release. Qoder-hosted external review is a separate, later revision.

## External review

Keep configured providers, exact model chains, effort and second-opinion rules. A reviewer's `model` is the dispatch value passed to the provider CLI; host metadata must report that value (family match for `claude-code` and `antigravity`, exact identity for `cursor-cli`) or, when `model` is a host alias routed elsewhere, the concrete id declared in `model_identity`. An unverified run records the served label with `model_verified: false` and stays inconclusive. Run `review external` after machine approval, at most one active call per service; different services can run concurrently. Antigravity is an external CLI provider only, not a coordinator host.

For a Cursor host-subagent external review, create `review external --dry-run`, retain `dry_run_path`, and give only that isolated packet to the reviewer. Import with paired `--from-result RESULT.json --from-dry-run DRY_RUN.json --actual-model "ACTUAL MODEL LABEL"`. The result must echo `review_binding` unchanged; actual model evidence comes from host metadata, never the reviewer's self-report. A required second opinion uses its own dry-run, different reviewer and binding. See [external-review.md](../skills/audit-literature-translation/references/external-review.md).
