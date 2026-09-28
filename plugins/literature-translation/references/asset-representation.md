# Asset Representation

## Asset representation

### Packets

Structured candidate formats are bound to source kind: `math` accepts `latex`, `table` accepts
`table`, and `code` accepts `code`. Figures and `mixed-region` assets remain original images; use
a source review correction to establish a more precise kind before requesting transcription.
Free text is not a replacement format for these kinds. Asset task scope includes semantic
dependencies outside the requested batch, matching QA.

Asset-audit packets bind `render_manifest` (relative render-directory paths to SHA-256) and
`render_manifest_sha256` into the packet identity. The manifest covers the comparison HTML,
copied MathJax runtime and original SVG/PNG files (fragments prepared by the 0.6.0 build may also
carry a per-region `original.pdf`; it is no longer written, linked or copied, and `source extract --replace`
removes it from the directories it re-exports). Review submissions must echo both
`render_artifact_sha256` and `render_manifest_sha256` from the packet after inspecting the actual
artifact. Imports and subsequent status queries verify all dependencies. Old packets without this
manifest require a new audit and cannot retain verified status; their candidates and history
remain available. Rebuilding a damaged render creates a new packet identity and requires a fresh
review, never silently repairs an old approval.

### Submission and review

Submit transcription through `assets submit PROJECT INPUT`; the envelope includes `packet_id`,
`author_task_id`, the packet's dispatch `model` and `reasoning_effort` echoed verbatim, `image_evidence`,
`candidates` and available `usage` (otherwise `null`); an optional `served_model_label` records the model the
host environment reported under that dispatch value (stored as is, unverified, never gated). A packet that
records no model or effort dispatched on the host's own default, so the echo of the absent field is omitted. Candidates name `asset_id`, `format` and `content`; `status` is
`candidate` or `unresolved`, with `notes` and `semantic_uncertainty` as needed. LaTeX content is a
math body without dollar delimiters; table content uses a rectangular `rows` array of cell
strings. Both candidate and review envelopes record actual viewing in `image_evidence` using the
packet's `required_images` path/hash map.

An asset reviewer has a different `reviewer_task_id` and returns `render_artifact_sha256` and
`render_manifest_sha256` plus one decision per asset, with `candidate_sha256`, `verdict`
(`accept`, `reject` or `unresolved`), `visual_checked` and `render_checked`; import using
`assets import-review PROJECT INPUT --confirm-visual-review` only when those checks were
performed. Stored review payloads must match their `review_sha256` before decisions are consumed,
imports replayed or revision context built; corrupt evidence cannot grant verified status. An
indexed review that cannot be validated blocks QA until renewed independent review; rebuilding its
audit packet uses a new identity and preserves the damaged historical file. Use
`assets status PROJECT` for the remaining queue.

### Revising a rejected candidate

Do not overwrite an earlier successful response or re-run an identical source-only packet. Create
a new correction packet with concrete review feedback:

```text
littrans assets packet PROJECT --asset-ids ID1,ID2 --revision-notes "Correct the independently reviewed delimiter and equation-separation defects."
```

The revision input binds to the previous candidate and review fingerprints. Give it to a fresh
transcriber with the original images and its recorded revision notes, submit its new response,
then request a new independent asset audit. Revision notes identify a defect; they are not an
authoritative answer and never replace viewing the source. An old cached response remains
reusable as historical evidence, but replaying it must not roll the active candidate back to an
older version. Until the correction is verified and rendered, reading retains the original image.

A new transcription candidate cannot clear an independent reviewer's semantic uncertainty. The
candidate carries that finding through further revisions, including direct asset packet
submission; only a valid new review decision can supersede it, and damaged review evidence
restores the pending block. Older recovery candidates read the finding from their immutable
revision context. A rejected/unresolved asset with semantic uncertainty remains pending recovery:
workflow transcription prioritizes its revision packet with the prior review feedback, while
ordinary image fallback without uncertainty remains a complete reading representation.

Damaged candidate records require fresh transcription and independent review; recovered packets
use a new identity and include only blocking assets. Interrupted candidate/review publication can
repair an invalid index mapping on replay, but cannot displace newer valid evidence.
