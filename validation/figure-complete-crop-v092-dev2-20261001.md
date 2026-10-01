# Complete composite Figure crops

Date: 2026-10-01. Build: 0.9.2-dev.2.

## Behavior and implementation

`fidelity._join_figure_panels` previously unified panel identity while retaining one
fragment per member region. `_original_html` emitted those fragments in sequence, so
browser wrapping changed the printed composition. The join now exports the union as one
raw PDF crop. PNG and SVG represent the same fragment; whitespace, curves, axes, condition
frames and internal labels are exported together. Explicit member glyph ownership is removed
so the exporter retains graphical content rather than exporting only text paths.

Caption ownership, whole-outline prose checks, separate caption checks and independently
captioned table checks run before the crop joins. Formula joins retain their existing
fragments. No renderer or CSS change is needed for the new single-fragment Figure.

## Regression coverage

`test_figure_complete_crop.py` exercises six panels arranged in four rows, including a
detector chart overlapping the two third-row image boxes. It checks one raw fragment,
complete panel coverage, both formats, pixel equality to an independent complete PDF crop,
caption text and parent association, body text separation and one Figure image in generated
HTML. Other tests check inherited member fragments/glyph ownership, prose in an empty
corner of the whole outline, and supported correction/replay of a legacy multi-fragment
record without granting approval.

Existing LT-096/LT-099 tests now assert complete Figure outlines. They retain cases for
different captions/figure numbers, side-by-side separately captioned stacks, intervening
text, distant panels, missing captions and independently captioned tables. Existing formula
tests continue to assert their multi-fragment behavior.

## Private real-page experiment

Source and recorded layout evidence were read from a private reference workspace and copied
into an ignored, disposable `tmp/figure-validation/` tree. Only that tree was written. No
installed plugin cache or reference-project state was changed. Hashes of the read source,
page ledgers, unit/asset registries and original images were unchanged after the experiment.
No source book, cropped image or private HTML is committed or included in distribution.

| Page | Before | Supported operation | Result |
| --- | --- | --- | --- |
| PDF255, Figure 11.1 | One Figure, five fragments | `source extract --pages 255-256 --replace` using recorded layout | One raw fragment; six plots in rows 2 + 1 + 2 + 1 |
| PDF256, Figure 11.2 | One Figure, two fragments with a recorded override | Replacement replayed the old decision; fresh `source review-packets` and `source import-review` replaced its region | One raw fragment; both vertical plots retained |

Figure 11.1's resulting bbox is `[94.5, 86.5, 380.5, 531.5]`; Figure 11.2's is
`[142.5, 86.0, 333.0, 373.5]`, in PDF points. Visual inspection of the original pages and
exported crops confirmed all plot frames, curves, axes, tick labels and condition boxes.
Both shared captions remain native units associated with their Figure and outside the image.
The PDF256 correction returned `changed_pages: [256]` and `approved_pages: []`.

## Browser verification

Playwright drove an isolated Edge browser through a loopback-only server. Both the formal
`source render` checkpoint and the normal `translation render --allow-draft --originals-only`
bilingual HTML were checked at 1280 x 1000 and 375 x 900 viewports. The standard reading
edition was a draft with missing translation labels; no source/translation approvals were
fabricated. The rendered image on each Figure was loaded, remained one DOM image and retained
the printed arrangement at both widths. There was no document-wide horizontal overflow.

| HTML / Figure | Wide displayed size | Narrow displayed size |
| --- | --- | --- |
| Source / 11.1 | 500.5 x 777.625 px | 335.109375 x 520.296875 px |
| Source / 11.2 | 333.375 x 502.09375 px | 333.375 x 502.09375 px |
| Bilingual / 11.1 | 486.1875 x 756.46875 px | 240 x 373.421875 px |
| Bilingual / 11.2 | 323.84375 x 488.734375 px | 240 x 362.203125 px |

Whole-Figure and page screenshots were visually inspected. The browser's only resource
error was a missing favicon; all Figure images loaded. Local snapshots, geometry JSON and
screenshots remain in the ignored scratch tree, outside distributed files. The browser and
loopback server were closed after verification.

## Compatibility and limits

The [migration guide](../plugins/literature-translation/MIGRATING.md) explains replacement,
reviewed region correction and fresh-project rebuild. Cached pages and pinned overrides
retain their old fragments until explicitly corrected; rendering alone cannot repair them.
`asset_crops` operates on one fragment and cannot collapse a Figure's fragment list.
Changed source identities require fresh source review and dependent translation validation.

Automatic grouping still requires usable shared-caption evidence and a text-free outline.
Ambiguous figures, legitimate sub-captions between panels, missing captions, cross-page
continuations and previously pinned crops require normal visual review/region decisions.
Small-window labels become smaller with the whole Figure, while the original SVG/PNG remains
available for inspection. This change does not claim approval of the private translation.

## Repository checks

- Release metadata/link validation, Ruff and strict mypy passed.
- `./scripts/check.ps1` passed: 1490 tests passed, 7 local-fixture tests skipped in 334.45 s,
  followed by successful runtime diagnostics (including the available layout runtime).
- Wheel, plugin ZIP and SHA-256 manifest built successfully in `tmp/figure-distribution/`;
  stable installation remained unchanged.
- `git diff --check` passed.

The initial full test run found one migration-guide formatting assertion (the guide must
name only the current target version). The guide was corrected and its ten related tests
passed before the successful final release-check run. The final reference hash check covered
59 files with no changes; ZIP inspection found no private PDF or validation-tree entries.
