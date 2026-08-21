# Host runtimes

Skills are host-neutral. The Python launcher in [runtime.md](runtime.md) is the same on every
supported agent. Hosts differ only in how they invoke skills and independent subagents.

Keep source PDFs, private workspaces, the plugin launcher, and external-review CLIs on the local
machine. Do not hand translation, audit, or external review to a cloud or remote subagent.

## Shared launcher

```text
python <plugin-root>/scripts/littrans.py doctor
```

Resolve `<plugin-root>` from the installed skill directory (`../..` from a skill folder). When
`littrans` is already on `PATH`, it is equivalent.

## Wave size

`workflow next` selects one consecutive same-stage wave. Limits depend on the coordinating host:

| Host | Default | Hard max |
| --- | ---: | ---: |
| Codex | 3 | 3 |
| Cursor | 6 | 9 |

Omit `--host` to auto-detect. Cursor is selected only when Cursor environment signals are present
and Codex signals are not; mixed or unknown environments stay on Codex. Pass `--host cursor` or
`--host codex` to override, and `--limit` only within that host's max.

After a wave is frozen, `workflow status`, packets, metrics, and combined `--batch-ids` renders
accept up to nine consecutive batches so a Cursor-selected wave can be coordinated as one set.
Assign one audit-lens reviewer across at most three consecutive batches; split a larger Cursor
wave into consecutive groups of three. Combined rendering remains optional.

## Codex

- Invoke a skill with `$skill-name`. Each skill's `agents/openai.yaml` supplies the Codex default
  prompt.
- Prefer a fresh, minimal-context Codex task or independent spawn for each writer and each audit
  lens. After `workflow next` selects a wave of at most three batches, coordinate it with
  `workflow status --batch-ids` instead of repeatedly scanning the project.
- Give a reviewer only its packet. Do not pass expected verdicts, prior findings, or translator
  rationale beyond recorded uncertainties.
- After installing or updating the plugin, start a new Codex task before continuing project work.

## Cursor

- Invoke a skill with `/skill-name` or by naming it in natural language.
- Use the plugin agents in `agents/` for isolated local work:
  - `literature-translator` writes one batch
  - `literature-fidelity-reviewer`, `literature-technical-reviewer`, and
    `literature-chinese-style-reviewer` run the three read-only audit lenses
  - `literature-external-reviewer` reviews one isolated `cursor-cli` dry-run packet
    for paired `--from-result` / `--from-dry-run` recording; it never edits target text
- Launch fresh, minimal-context local Task subagents in parallel when the host allows it. Cursor
  waves default to six batches and may include up to nine; run one writer per batch and chunk
  each audit lens into consecutive groups of at most three batches. After a wave is selected,
  coordinate it with `workflow status --batch-ids`. Reviewer agents are read-only. The three
  audit-lens reviewers return JSONL issue records in the final message, including an empty body
  when there are no findings; the parent writes that content and runs `review import-set`.
  `literature-external-reviewer` instead returns one JSON object with `review_binding`, `verdict`,
  `summary`, and `issues`, using an empty `issues` array when there are no findings. The parent
  records it with paired `--from-result` and `--from-dry-run` inputs. Do not ask any reviewer to
  create files. Do not use Cursor Cloud Agents or `/in-cloud` for this workflow.
- After installing or updating the plugin, reload the window and start a new agent session.

## External review

External review is a project-configured local CLI gate (`claude-code`, `antigravity`, or
`cursor-cli`). Run `littrans review external` after machine approval. Separate services may
run concurrently, but keep at most one active call per service; the CLI enforces that limit
across processes.

The `cursor-cli` driver runs Cursor Agent in plan mode inside the isolated temporary review
workspace and consumes its `stream-json` protocol for actual-model and usage evidence. Cursor's
sandbox flag is unavailable on Windows, so the driver relies on plan mode and the isolated
workspace there. Exact Cursor model IDs encode effort and fast mode. Configured model fallbacks
remain within one Cursor reviewer; quota failures advance through the configured chain before the
workflow considers another reviewer.

When the coordinating host is already Cursor, a `cursor-cli` review may be performed by a local
host subagent that receives only the isolated dry-run packet. First run `review external --dry-run`
and retain the returned `dry_run_path`. Record the result with
`littrans review external PROJECT BATCH --reviewer ID --from-result RESULT.json --from-dry-run DRY_RUN.json --actual-model "ACTUAL MODEL LABEL"`.
The trusted host coordinator takes the actual label from Cursor task metadata; do not ask the
reviewer model to self-report it. The local reviewer must echo the packet's `review_binding` in
its result JSON. The CLI rejects obsolete, unbound, tampered, mismatched, or incorrectly attested
imports. If a second opinion is required, repeat the dry-run and paired import with
`--second-opinion`, a different reviewer, and that task's actual model label. Do not hand the packet
to a cloud or remote subagent. Claude Code and Antigravity reviews still use their CLIs.
