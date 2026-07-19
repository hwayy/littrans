---
name: finalize-literature-translation
description: Finalize reviewed littrans batches by enforcing QA and audit gates, checking issue resolutions, granting machine-reviewed status, recording explicit human approval, and rendering private Chinese Markdown and bilingual HTML. Use only after translation and independent audit are complete.
---

# Finalize Literature Translation

Enforce the state machine; do not bypass it for a cleaner-looking output.

Use the bundled Python launcher described in `../../references/runtime.md` for every `littrans` command.

## Procedure

1. Run `source verify`, `status`, `review status`, and `qa run` for every requested batch. Stop if source verification is stale or fails.
2. If blocker or major issues remain open, stop and return them to `translate-literature-section`. After revision, require new passing QA and targeted audit before continuing.
3. Confirm that every accepted issue is resolved and every rejected or waived issue records a reason. Follow [release-gates.md](references/release-gates.md).
4. Run `approve --level machine` only when QA passes, an audit marker covers all three lenses, and no blocker/major issue is open.
5. Run `approve --level human --confirm-user-approved` only after the user explicitly approves the reviewed text. Never infer human approval from silence or successful tests.
6. Run `render` without `--allow-draft`. Inspect Markdown and responsive bilingual HTML. Confirm formulae render from LaTeX rather than images, tables are local structured content, code indentation/highlighting is intact, literal tags are inert, Note blocks stand out, continuations have no false paragraph break, and figures have translated labels. Inspect dark/mobile layouts.
7. Return the reading artifacts, quality summary, unresolved decision report, approval level, and any remaining minor/suggestion issues.

## Prohibitions

- Do not edit translation text during finalization.
- Do not close an issue merely to unblock rendering.
- Do not publish, upload, or label the output for public distribution; the first-round workflow is private-research-only.
