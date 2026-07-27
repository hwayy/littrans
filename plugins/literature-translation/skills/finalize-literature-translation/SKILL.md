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
5. If `external_review.enabled` is true, run `review external`, resolve every valid blocker, major, and minor issue through the translation workflow, then return the current revision to the same reviewer. Run `approve --level external` only when `review external-status` is accepted, model evidence and translation fingerprint are current, and no substantive issue remains open.
6. Run `approve --level human --confirm-user-approved` only after the user explicitly approves the reviewed text. Never infer human approval from silence, machine acceptance, external acceptance, or successful tests.
7. Run `render --batch-id <id>` without `--allow-draft` for batch delivery, or use `--pages` only for an intentionally page-scoped collection. Require the generated render-QA report to pass. Inspect Markdown and responsive bilingual HTML. Confirm omitted source matter is absent, formulae render from LaTeX rather than images, tables are local structured content, code indentation/highlighting is intact, literal tags are inert, Note blocks stand out without duplicated wrappers, titled sidebars retain their grouped title/body treatment, continuations have no false paragraph break, and figures have translated labels. Inspect dark/mobile layouts.
8. Return the reading artifacts, quality summary, unresolved decision report, approval level, external-review summary when configured, and any remaining suggestion issues.

## Prohibitions

- Do not edit translation text during finalization.
- Do not close an issue merely to unblock rendering.
- Do not publish, upload, or label the output for public distribution; the first-round workflow is private-research-only.
