# Host runtimes

LitTrans supports local Codex and Cursor orchestration through the launcher in [runtime.md](runtime.md). Keep source PDFs, project state and review workspaces on the local host. External reviewer services remain controlled by project configuration.

## Scheduling and models

| Host | Batch wave default | Batch wave maximum | Translation and transcription |
| --- | ---: | ---: | --- |
| Codex | 3 | 3 | Fresh `gpt-5.6-luna`, `max` effort |
| Cursor | 6 | 9 | Explicit host-available role model configuration |

A wave is a coordination scope, not permission to exceed the host's active task capacity. Independent transcribe and translate tasks receive the same source text and original images with no shared candidate output. Queue work when slots are full; a finished translation may enter its audit while other work continues.

`workflow next` auto-detects the host; mixed or unknown environments use Codex. Explicit `--host codex` or `--host cursor` overrides detection. Once selected, keep batch IDs fixed and use `workflow status --batch-ids`. Assign a translation audit lens across at most three consecutive batches; split larger Cursor waves accordingly.

Model/effort metadata comes from the host. When the required model is unavailable, report the configuration problem; do not silently substitute. Existing review-role model configurations remain in force. Fresh task context is required for independence; a task fork that contains another worker's candidate or expected verdict is not independent.

## Codex

Invoke `$skill-name`; UI metadata lives in each skill's `agents/openai.yaml`. Use the local host's independent subagent mechanism for the two production lanes and separate reviews. Provide only the assigned packet and necessary original images. Start a new task after changing the installed plugin version; do not remove caches used by a running task.

## Cursor

Invoke `/skill-name` or its natural-language name. Plugin agents provide `literature-translator`, `literature-transcriber`, `literature-asset-reviewer`, the three translation audit lenses and `literature-external-reviewer`. Launch fresh local Task subagents; do not use Cursor Cloud Agents or `/in-cloud`.

Reviewers are read-only. Translation lenses return JSONL issues (an empty result still needs import). Asset reviewers return the packet's bound review JSON. The parent persists and imports those responses. Translators and transcribers write only their assigned output and submit through the CLI. Reload the window and start a new session after a plugin update.

## External review

Keep configured providers, exact model chains, effort and second-opinion rules. Run `review external` after machine approval, at most one active call per service; different services can run concurrently. Claude Code and Antigravity are external CLI providers, not supported coordinator hosts.

For a Cursor host-subagent external review, create `review external --dry-run`, retain `dry_run_path`, and give only that isolated packet to the reviewer. Import with paired `--from-result RESULT.json --from-dry-run DRY_RUN.json --actual-model "ACTUAL MODEL LABEL"`. The result must echo `review_binding` unchanged; actual model evidence comes from host metadata, never the reviewer's self-report. A required second opinion uses its own dry-run, different reviewer and binding. See [external-review.md](../skills/audit-literature-translation/references/external-review.md).
