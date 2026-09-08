# Original glyph export: unrelated SVG groups

QASC physical pages 211 and 220 contain plot/circuit SVG groups elsewhere on the page. The old exporter rejected any group, causing explicitly selected inline glyph regions to fall back to rectangular crops containing neighboring ink.

The exporter now renders the intact group with the original definitions, transforms and clips, measures its painted bounds (including raster images), and excludes it only when every bound is outside the selected fragment. Intersecting or unmeasurable groups still fail closed. It does not flatten or infer unknown vector content.

Validation: tests/test_glyph_export.py: 7 passed. New regression covers distant transformed groups and intersecting groups that must still fail. Actual QASC source probe: all 19 inline assets on p211 and all 33 on p220 exported successfully without raw-region fallback. Actual production visual review is recorded separately in QASC; probe success alone is not source approval.

Modified files: src/littrans/glyph_export.py and tests/test_glyph_export.py. Not committed. Parent repository-wide test suite not run for this change.
