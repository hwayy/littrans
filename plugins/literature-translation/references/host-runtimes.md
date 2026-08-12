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
- Prefer a fresh Codex task or an independent spawn for each writer and each audit lens.
- Give a reviewer only its packet. Do not pass expected verdicts, prior findings, or translator
  rationale beyond recorded uncertainties.
- After installing or updating the plugin, start a new Codex task before continuing project work.

## Cursor

- Invoke a skill with `/skill-name` or by naming it in natural language.
- Use the plugin agents in `agents/` for isolated local work:
  - `literature-translator` writes one batch
  - `literature-fidelity-reviewer`, `literature-technical-reviewer`, and
    `literature-chinese-style-reviewer` run the three read-only audit lenses
- Launch independent local Task subagents in parallel when the host allows it. Reviewer agents are
  read-only: they return JSONL issue records in the final message, including an empty body when
  there are no findings. The parent writes that content and runs `review import-set`. Do not ask a
  reviewer to create files. Do not use Cursor Cloud Agents or `/in-cloud` for this workflow.
- After installing or updating the plugin, reload the window and start a new agent session.

## External review

External review is a project-configured local CLI gate (`claude-code` or `antigravity`), not a
Codex or Cursor subagent. Run `littrans review external` after machine approval. Do not substitute
the host agent for that CLI.
