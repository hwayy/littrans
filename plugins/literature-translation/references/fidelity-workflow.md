# Fidelity workflow and recovery

Schema 6 uses one source preparation path. Native prose and layout regions produce immutable source-owned `{{asset:ID}}` references; original vector PDF/SVG and high-resolution PNG evidence remain available throughout the workflow. A detector warning requires a source decision, not an automatic formula-recognition retry.

## Independent states

- Source fidelity: all selected content has reviewed ownership, reading order and complete boundaries, bound to the source fingerprint.
- Translation: submitted prose, deterministic QA, three independent audit lenses, configured external review and optional explicit human approval.
- Asset representation: original image, structured candidate, independent visual/render review, reliable candidate or original-image fallback.

The second and third states advance independently after source fidelity. Missing LaTeX never blocks an otherwise reviewed translation. Missing understanding or source content does. Nonempty recorded translation `uncertainties` block QA for the affected unit and its semantic dependencies; resolve the actual understanding problem and resubmit before approval. Merely unfinished LaTeX belongs to asset progress and must not be recorded as an unresolved translation-understanding problem. Source/content/ownership changes invalidate affected translation dependencies; a display-only change invalidates rendering evidence rather than unrelated prose audits.

## Narrow original-glyph corrections

When a region includes neighboring prose, a source reviewer may explicitly name its existing PDF `glyph_ids`. The narrow glyph exporter copies the selected original PDF vector paths and supported nearby horizontal rules into the reading SVG and model PNG; it performs no OCR, character substitution or LaTeX inference. The separate original PDF fragment remains raw-region evidence. This correction requires inspected ownership and a new packet-bound source review; it is not an automatic formula recognizer.

Bounds include the actual visible vector ink, because font-reported boxes can put accents or italic overhangs outside their nominal rectangle. SVG dimensions, PNG export and baseline placement use the expanded bounds. Unmapped glyphs, unsupported SVG groups/transforms or vector primitives fail closed. Retain the previous original region, or issue a new complete raw-region correction without explicit glyph filtering, and review that fallback; never approve a silently incomplete filtered image.

Footnotes keep `footnote_number` on the footnote unit and `footnote_refs` on its calling unit. References contain stable source unit IDs, each resolving to a `footnote` unit, without duplicates. Preserve the number and relationship separately from prose. Source review and audit dependencies include both caller and footnote, including cross-page links; a changed footnote invalidates its dependent caller's evidence. HTML provides links to the original footnote unit anchors and displays its recorded number.

## Packets and imports

Use the emitted schemas as the authority for exact fields. `workflow packet` stages are `transcribe`, `translate`, `asset-audit` and `audit`. All model work reads original image evidence. A translate packet does not consume unverified transcription candidates.

Submit transcription through `assets submit PROJECT INPUT`; the envelope includes `packet_id`, `author_task_id`, actual `model`, `reasoning_effort`, `image_evidence`, `candidates` and available `usage` (otherwise `null`). Candidates name `asset_id`, `format` and `content`; `status` is `candidate` or `unresolved`, with `notes` and `semantic_uncertainty` as needed. LaTeX content is a math body without dollar delimiters; table content uses a rectangular `rows` array of cell strings. Both candidate and review envelopes record actual viewing in `image_evidence` using the packet's `required_images` path/hash map. An asset reviewer has a different `reviewer_task_id` and returns `render_artifact_sha256` plus one decision per asset, with `candidate_sha256`, `verdict` (`accept`, `reject`, or `unresolved`), `visual_checked` and `render_checked`; import using `assets import-review PROJECT INPUT --confirm-visual-review` only when those checks were performed. Use `assets status PROJECT` for the remaining queue.

Translation records retain their unit's `source_hash`, references and an `image_evidence` map of inspected original image path to SHA-256 from `original-images.json`. Supplementary translations of image-contained language live in `asset_translations`, keyed by `asset_id`, using `target_text`, `target_table` or `figure_labels`. Declare `language_present: false` with explanatory `notes` only for an asset with no translatable natural language. Asset candidate content never replaces the source reference ID. Do not put raw LaTeX in translated image companions (`asset_translations`); preserve references and submit mathematical candidates through the independently reviewed asset channel.

## Resume

Persist successful responses before parsing or import. A cached response is reusable only for the same input, model, prompt and relevant output fingerprints. Recover serialization failures offline; do not pay for an identical successful response again. Imports are idempotent, but changed source or packet hashes require fresh evidence. Record actual host/model metadata and measured usage; token or monetary costs unavailable from the host stay unknown.

After interruption, ask status for the frozen batch IDs, recover unimported successful responses, then dispatch only missing stages. Preserve unresolved original-image assets after translation rendering; this is a recoverable checkpoint, not a failed reading artifact.


## Revising a rejected asset candidate

Do not overwrite an earlier successful response or re-run an identical source-only packet. Create a new correction packet with concrete review feedback:

```text
littrans assets packet PROJECT --asset-ids ID1,ID2 --revision-notes "Correct the independently reviewed delimiter and equation-separation defects."
```

The revision input binds to the previous candidate and review fingerprints. Give it to a fresh transcriber with the original images and its recorded revision notes, submit its new response, then request a new independent asset audit. Revision notes identify a defect; they are not an authoritative answer and never replace viewing the source. An old cached response remains reusable as historical evidence, but replaying it must not roll the active candidate back to an older version. Until the correction is verified and rendered, reading retains the original image.
