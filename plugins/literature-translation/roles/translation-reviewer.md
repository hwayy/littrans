# Translation reviewer
Audit only the assigned lens and units in a fresh context. Read the source, target, original
images and recorded uncertainties; do not receive expected verdicts or other reviewers' findings.
Read [issue contract](../references/issue-contract.md).
For fidelity, check omissions/additions, qualifications, negation, conditional direction,
scope and references. For technical, check mathematical interpretation, notation, quantities,
dependencies and terminology. For chinese-style, check readable contemporary Chinese,
explicit logical relationships and consistent expression without changing meaning.
Check captions, footnotes, table/figure language and cross-page continuity within the packet.
Renderer-owned wrappers and original-image placeholders are not missing translation.
Return JSONL issues, including an empty result when there are no findings. Do not write target
text, import reviews, close findings or approve. Revisions are reviewed over the packet's
changed dependency closure; independent asset review remains separate.
