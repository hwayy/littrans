# Original-image reading follow-up

The seven-page QASC workflow exposed a stale-record defect: nontranslatable equation units still had historical translations keyed by the same unit ID. Rendering could reference deleted historical assets, and audit packets could display obsolete equation text. Both rendering and audit-text assembly now ignore translation records for nontranslatable units; historical records remain preserved. Updated audit packets were generated before reviewer imports.

In `--originals-only` mode, each original image links directly to its original PDF/PNG. Repeated status/link labels are omitted so inline expressions read continuously. Candidate approval state remains unchanged and candidates are never inserted.

Validation: 6 workflow-v6 tests passed after the stale-record fix; 28 asset-representation tests passed after the clickable-original change. The latter emitted only a pytest cache write warning. A real headless Chrome check of the three-batch draft loaded all226 image elements with zero broken images, zero candidate elements, no raw asset tokens and no document overflow at1400px. The desktop screenshot was actually inspected. A later four-page draft with the table loaded296 image elements at430px; its six-column Chinese table scrolls within its cell. These are draft-layout checks, not final translation approval.

A further independent audit exposed caption normalization consuming the dot inside chapter-based identifiers (Table1.1 / Figure12.3). Caption-number regex now preserves dot-separated numbers. 18 caption-focused tests passed. The audit importer correctly rejects pre-fix caption fingerprints, so affected packets were regenerated for current independent coverage. Original submitted translations were already correct.

Final structure/workflow-v6 regression after range labels and explicit alphabetical list marker display:19 tests passed. Noncontiguous page headers now list actual pages, and theorem (a)/(b) labels do not receive an extra bullet.
