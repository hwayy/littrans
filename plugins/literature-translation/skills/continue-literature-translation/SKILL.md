---
name: continue-literature-translation
description: Coordinate the LitTrans fidelity-first workflow with independent parallel transcription and translation, separate asset review and three translation audit lenses. Use to resume or advance batches on Codex or Cursor.
---

# Continue Literature Translation

Use [host-runtimes.md](../../references/host-runtimes.md) for local execution and [fidelity-workflow.md](../../references/fidelity-workflow.md) for state and recovery.

1. Recover the requested resume boundary from the durable checkpoint. Select a bounded wave with `workflow next PROJECT --start-at ID`, adding `--through ID` for a fixed endpoint. An unbounded selection is valid for a new project. Do not restart historic unfinished batches outside the user's scope.
2. Keep the selected batch IDs fixed and use `workflow status PROJECT --batch-ids IDS` for their separate dependencies. Codex waves contain at most 3 batches; Cursor defaults to 6, maximum 9. Wave size is not the simultaneous child-task count: obey actual host capacity.
3. For each fidelity-ready batch, create separate `--stage transcribe` and `--stage translate` packets. Dispatch fresh independent workers in parallel; both see original English and images, neither sees the other's candidate. Codex writers and transcribers use `gpt-5.6-luna` at `max` effort. Target about 900 source words and a soft limit of 60 assets, cutting at logical boundaries rather than inside a derivation.
4. Submit the two outputs separately. Once a translation passes QA, issue `--stage audit --lens all` packets immediately. Run the three lenses independently in consecutive groups of at most three batches. Separately issue `--stage asset-audit` to a reviewer distinct from the transcriber, with original images and candidate render evidence.
5. Import all reviews, including empty translation-lens results. Consolidate valid translation issues into one revision, then run the missing closure checks returned by status. Keep uncertain assets as original images. For a rejected candidate, create a new `assets packet --asset-ids IDS --revision-notes "specific feedback"` and schedule a fresh transcriber plus independent asset re-audit; reuse the original cached response only as history. Do not wait for LaTeX completion to audit an otherwise understandable translation.
6. Machine-approve only with current source fidelity, passing QA, all three translation lenses and resolved blocker/major issues. Preserve configured external reviewer models, service concurrency and required second opinions. Follow the audit skill's external-review reference.
7. Render each batch independently after its configured gate and cross-batch dependencies pass. Check the original-image fallback and asset status separately from translation status. Persist the next in-scope resume boundary and the outstanding asset queue; rendering a translation does not mean transcription is complete.

Cache successful outputs immediately, import idempotently and repair serialization offline before retrying model calls. Record unknown usage as unknown. Do not reuse stale packet bindings or a reviewer's self-reported model as host evidence.
