# Literature Translation 0.6

LitTrans provides one resumable workflow for translating English technical books and research papers into Simplified Chinese on Codex, Cursor and Claude Code:

**Probe document structure → preserve the source faithfully → translate with original images → audit translation → optionally enhance assets independently → render a reading edition.**

Formula recognition is deferred until faithful text and original-image assets are available. Translation reads the original images with full paragraph context; it does not wait for LaTeX. Original images remain available even after a structured candidate is verified.

## First use

Requires Python 3.12 or later. Run `python <plugin-root>/scripts/littrans.py doctor`; the launcher manages a private environment outside the plugin and project. Source preparation also requires the isolated layout detector; `doctor` reports it under `layout_runtime`, and `layout install` sets it up. See [runtime.md](references/runtime.md) and [host-runtimes.md](references/host-runtimes.md).

Initialize a new private project with `project init`, or rebuild an older project with `project rebuild OLD NEW`. Schema 6 does not write into older project schemas. Rebuild copies the PDF, project context and glossary, leaving historical outputs and reviews in the old project. See [MIGRATING.md](MIGRATING.md).

## The workflow

1. **Probe and prepare.** Run `source probe PROJECT --pages PAGES`, inspect representative originals and complete the document-specific [structure profile](references/document-structure.md). Use it to guide extraction and supported corrections. `source prepare` combines native glyph geometry and the required isolated layout detector. It saves prose plus stable `{{asset:ID}}` references and SVG/PNG originals. Whole circuit diagrams remain intact. A missing detector or text layer is visible in source review, not an invitation to silently omit content.
2. **Verify fidelity.** `source review-packets`, `source import-review` and `source verify` check original-page coverage, reading order, crop completeness, numbering and source ownership. A full-page fallback alone does not prove completeness. Formula LaTeX is not part of this gate. `source render` then writes a readable HTML checkpoint of the verified source with the original assets inline for a final human look before translation.
3. **Translate; optionally enhance assets.** Formula transcription is optional and can be scheduled after the reading edition is complete. `workflow packet --stage transcribe` and `--stage translate` provide the same source context and original images to fresh tasks. Both use the role models configured per host in the project's `agent_models` (seeded from `profiles/host-models.yaml`). Translators preserve asset references and record the original images actually inspected; transcribers submit separate structured candidates.
4. **Review independently.** `--stage asset-audit` compares candidates and their renders with original images. Translation retains fidelity, technical/terminology and Chinese-expression lenses via `--stage audit --lens all`, followed by configured external review. Neither confidence nor compilation substitutes for visual review.
5. **Read.** A reviewed translation can render while LaTeX remains unfinished. The shared asset resolver uses a verified, renderable candidate or the complete original image with an unfinished status. Offline MathJax and original-image fallback protect reading when typesetting is unavailable.

Use `continue-literature-translation` to coordinate this workflow. Codex and Claude Code waves default to three batches (Claude Code maximum six); Cursor defaults to six, maximum nine. Actual simultaneous tasks obey host capacity. Batches target about 900 source words and a soft limit of 60 assets without splitting a logical derivation.

## Commands and evidence

```text
littrans project rebuild OLD NEW
littrans source probe PROJECT --pages 1-3
littrans source prepare PROJECT --pages 1-3
littrans source review-packets PROJECT --pages 1-3
littrans source verify PROJECT --pages 1-3
littrans source render PROJECT --pages 1-3 [--standalone]
littrans batch create PROJECT --pages 1-3 [--prefix NAME] [--unit-ids ID1,ID2] [--untranslated-only]
littrans batch refresh PROJECT BATCH_ID
littrans workflow next PROJECT
littrans workflow status PROJECT --batch-ids ID1,ID2
littrans workflow packet PROJECT --stage transcribe --batch-ids ID1
littrans workflow packet PROJECT --stage translate --batch-ids ID1
littrans assets submit PROJECT CANDIDATES.json
littrans workflow packet PROJECT --stage asset-audit --batch-ids ID1
littrans assets import-review PROJECT REVIEW.json --confirm-visual-review
littrans assets status PROJECT
littrans workflow packet PROJECT --stage audit --lens all --batch-ids ID1
littrans review import-set PROJECT PACKET/manifest.json ISSUES.jsonl
littrans review issues PROJECT ID1 [--all] [--jsonl]
littrans workflow packet PROJECT --stage revise --batch-ids ID1
littrans review resolve PROJECT ID1 ISSUE_ID[,ISSUE_ID...] --resolution "..."
littrans render PROJECT --batch-id ID1
```

`batch create` cuts verified pages into batches at logical boundaries (about 900 source words, a soft limit of 60 assets, complete `parent_id` groups); `workflow next` requires at least one batch. `review import-set` canonicalizes reviewer issue ids to `audit-<hash>` and keeps the reviewer id as `source_issue_id`; `review resolve` accepts either. `workflow status` reports `audit_stale` reasons when brief/style-guide/glossary edits or changed units reset audit coverage. A `revise` packet carries the current translation and open issues for one fresh revision. Batch sets may mix series when their units do not overlap. A project with no transcription candidate renders originals-only automatically.

Use command help and the emitted packet schemas for exact import fields. [fidelity-workflow.md](references/fidelity-workflow.md) describes bindings and recovery. Save successful responses before import; identical imports are idempotent. Report source fidelity, translation approval, reliable structured coverage and fallback proportion separately. Unknown tokens or fees stay unknown.

## Boundaries

Source meaning or asset ownership changes invalidate affected translations and their review dependencies. Display-only candidate improvements preserve stable asset IDs and require current rendering evidence. A reviewer cannot approve their own transcription. Explicit human approval remains distinct from machine or external review.

Source PDFs, images, model responses, translations and private workspaces stay outside the public plugin repository. The plugin is for private research reading; a rendering command does not publish it. The CLI orchestrates evidence and host tasks; it does not require a separate commercial model account for transcription.
