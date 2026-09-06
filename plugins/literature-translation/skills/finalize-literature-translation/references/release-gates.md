# Reading release gates

`machine-reviewed` requires current source-fidelity review, complete selected translation coverage, current source hashes and image evidence, passing deterministic QA, all three independent translation audit lenses for the current revision, and no open blocker/major issue. A revision that changes meaning requires the affected audit/dependency closure.

A reliable structured asset additionally requires an independent reviewer, current source/candidate bindings and successful visual comparison of its rendered candidate with the original image. Compilation or similarity alone does not establish mathematical correctness. A pending/rejected/unrenderable candidate retains the complete original with an unfinished status and does not block an otherwise reviewed translation.

Configured external review remains required after machine approval. Its accepted result must bind to the current translation fingerprint with verified actual-model evidence, required second opinions, and no open substantive issue under that project's policy. Projects without external review use the machine-review gate. Human approval requires explicit user approval of the text and the guarded CLI flag; external acceptance is never human approval.

Formal rendering runs without `--allow-draft` and includes private-use and quality metadata. Check offline MathJax, image fallback, baseline/scaling, original access, captions, footnotes and cross-page dependencies. Report source fidelity, translation approval and reliable transcription coverage separately.
