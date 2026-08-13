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

## Codex

- Invoke a skill with `$skill-name`. Each skill's `agents/openai.yaml` supplies the Codex default
  prompt.
- Prefer a fresh, minimal-context Codex task or independent spawn for each writer and each audit
  lens. After `workflow next` selects a wave, coordinate it with `workflow status --batch-ids`
  instead of repeatedly scanning the project.
- Give a reviewer only its packet. Do not pass expected verdicts, prior findings, or translator
  rationale beyond recorded uncertainties.
- After installing or updating the plugin, start a new Codex task before continuing project work.

## Cursor

- Invoke a skill with `/skill-name` or by naming it in natural language.
- Use the plugin agents in `agents/` for isolated local work:
  - `literature-translator` writes one batch
  - `literature-fidelity-reviewer`, `literature-technical-reviewer`, and
    `literature-chinese-style-reviewer` run the three read-only audit lenses
- Launch fresh, minimal-context local Task subagents in parallel when the host allows it. After a
  wave is selected, coordinate it with `workflow status --batch-ids`. Reviewer agents are
  read-only: they return JSONL issue records in the final message, including an empty body when
  there are no findings. The parent writes that content and runs `review import-set`. Do not ask a
  reviewer to create files. Do not use Cursor Cloud Agents or `/in-cloud` for this workflow.
- After installing or updating the plugin, reload the window and start a new agent session.

## External review

External review is a project-configured local CLI gate (`claude-code` or `antigravity`), not a
Codex or Cursor subagent. Run `littrans review external` after machine approval. Do not substitute
the host agent for that CLI. Separate services may run concurrently, but keep at most one active
call per service; the CLI enforces that limit across processes.
