# Asset Representation

## Asset representation

### Interface contracts

[Asset packets and submissions](cli-reference.md#asset-contract) define formats, evidence,
render bindings and independent decisions. Use the emitted packet and response schema.

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
