# PR #10 twenty-ninth review follow-up — 2026-09-14

Five findings were reproduced and fixed; two documentation observations were handled as well.

- `_separate_display_units` dropped `footnote_refs` from the prose chunks it rebuilt around a
  display formula. A native block with a display and a footnote call made `prepare_source`
  commit `units.jsonl` and then fail in `build_source_review_packet`, leaving every
  `_current_units` caller (verify, packets, import-review) broken. Chunks now keep the links
  whose call they carry (plan note numbers are passed before structure assembly), and
  `_validate_footnote_relationships` runs inside the authority transaction before publishing.
  Reproduced end to end with a generated PDF (TextWriter block: display line + prose with a
  superscript call, detector-labelled footnote) and a stubbed layout result.
- QA v6.14: `empty-translation` is reported when source prose beside placeholders has no
  target text; asset-only source blocks may still translate to their placeholders.
- Receipt-only resubmissions (same content, different `image_evidence`) update the receipt in
  place without `revised` status or audit invalidation; the cached QA pass becomes stale.
- `fidelity-workflow.md` restructured by topic with the source `override` contract and example;
  the verify skill links to it; stage list fixed; README command list gains `batch create` /
  `batch refresh`; translation-record example uses `evidence/pages/...` and
  `derived/assets/fidelity/...` paths; CLI id lists are trimmed.

`tests/test_pr10_round29.py` adds ten cases (unit-level split, plan-number split, end-to-end
prepare/approve/verify, transaction rollback, QA omission and asset-only acceptance, receipt-only
resubmission, CLI trimming, documentation contract checks). No real model installation or
production translation was performed; project schema remains 6.
