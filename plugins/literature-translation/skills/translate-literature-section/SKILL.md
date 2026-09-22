---
name: translate-literature-section
description: Translate or revise one faithful LitTrans source batch into Simplified Chinese while preserving original asset references. Use after source coverage verification; translation proceeds independently of LaTeX transcription.
---

# Translate Literature Section

Act as the only writer of target text for the assigned batch. Use [runtime.md](../../references/runtime.md), [host-runtimes.md](../../references/host-runtimes.md) and [translation-quality.md](references/translation-quality.md).

1. Read the translate packet's source, context, output schema, glossary and `original-images.json`. Inspect the original page and asset PNGs needed to understand every source unit. Writers run in a fresh task using the model and effort recorded in the packet (the `translate` role of the project's `agent_models.<host>`); where the packet records neither, the host's own default applies. Do not wait for or default to reading transcription candidates.
2. Translate only assigned translatable units. Preserve `unit_id`, `source_hash` and every `{{asset:ID}}` occurrence within its source-owned target block. Chinese may reorder references inside a block; never move a clause or reference to another unit. Keep display assets in their original structural location.
3. Submit target prose separately from formula, table or code candidates. Use `asset_translations` entries keyed by `asset_id` for table cells (`target_table`), corresponding region prose (`target_text`) or figure labels (`figure_labels`). Set `language_present: false` only with an explanation in `notes` when the original contains no translatable natural language. Do not replace assets with guessed LaTeX or unreviewed reconstructions.
4. Write `image_evidence` as a map from the packet's actually inspected image paths to their supplied SHA-256 values. Copying a manifest without viewing the images is not evidence. Record unresolved mathematical understanding in `uncertainties`; it blocks the affected translation. An understood original formula with unfinished LaTeX does not.
5. Preserve approved terminology. Put term proposals, uncertainties and reader notes in their separate fields. Verify any allowed current-technology reader note against a current primary source and include its HTTPS URL/access date; otherwise omit it.
6. Run `translation submit` and `qa run`. Fix deterministic errors without weakening reference or ownership checks; treat the `target-halfwidth-punctuation` and `asset-reference-spacing` warnings as defects to fix. For an audit revision, the `revise` packet adds `<batch>.translation.jsonl` (current records), `<batch>.issues.jsonl` (open issues) and `<batch>.revise.md`: address every open issue, sweep the whole batch for the same defect class, resubmit the full batch, rerun QA and report the addressed issue ids (and any deliberately unchanged, with reasons) for the coordinator to resolve. Do not resolve issues yourself.

Do not change source units, assets, approved terminology or reviewer evidence. Report QA and unresolved issues; passing QA does not grant approval.

Recorded `uncertainties` mean unresolved understanding and block QA for the affected translation and its dependencies. Resolve the question using the original context and resubmit; do not clear the field solely to pass QA. Pending LaTeX alone is asset progress, so a faithful translation may keep the original reference without declaring a meaning uncertainty.
