---
name: finalize-literature-translation
description: Finalize reviewed LitTrans batches and render Chinese Markdown plus bilingual HTML with verified structured assets or faithful original-image fallback. Use after translation QA and independent audit.
---

# Finalize Literature Translation

Use [runtime.md](../../references/runtime.md) and [release-gates.md](references/release-gates.md). Source fidelity, translation approval and structured-asset verification are separate facts.

1. Check current `source verify`, `workflow status`, `review status`, `assets status` and `qa run` for the requested batch. Resolve stale source/translation evidence and open blocker/major translation issues through the appropriate earlier stage.
2. Machine-approve only after all three translation audit lenses cover the current revision. Preserve configured external review and second-opinion gates. Record human approval only after explicit approval of the reviewed text; never infer it from successful checks.
3. Render one batch with `render PROJECT --batch-id ID` without `--allow-draft`. The default short batch output name is ownership checked. Use a unique `--name` on collision. Combined `--batch-ids` (batch series may mix when units do not overlap) or `--pages` rendering is for an explicitly requested collection. The edition header, `*.quality.md` and `render-qa.json` (`rendered_status`, `review_batch_ids`) describe the rendered batches, not the project-wide status.
4. Inspect responsive bilingual HTML and Markdown with original assets. A project without any transcription candidate renders originals-only automatically (`originals_only_reason: no-transcription-candidates` in the render QA); when the user requests an all-original edition of a project that has candidates, pass `--originals-only`. Originals-only means every formula uses its original image and links to its high-resolution source; per-formula transcription-status clutter is omitted and candidate review state is untouched. Unverified or unrenderable candidates must display the complete original and “转写未完成／待核验”. Verified, successfully rendered candidates may replace that display, but original images must remain accessible. Local MathJax and fonts must work offline; a loader or typesetting failure must retain the image.
5. Check inline baselines and scaling, narrow-screen matrices, equation numbers, captions, footnotes, cross-page structures, table-region translations and literal markup. A complete original table is acceptable while reconstruction remains unverified; its meaningful language still needs translation or an explicit unresolved status.
6. Return artifact paths, translation approval level, reliable structured coverage, original-image fallback proportion, unresolved issues and available cost/review-work evidence. Do not label all-image output “complete LaTeX transcription”.

Do not edit translation text or close issues during finalization. Formal artifacts are private research outputs; publication is a separate user action.
