# Fidelity equation-number rendering correction

Date: 2026-09-07

Problem: a fidelity complex unit may preserve its equation number in source/target text and also carry equation_number metadata. The HTML and Markdown renderers appended the metadata number even when the exact parenthesized number was already in the preserved content, producing duplicate labels.

Change: in the fidelity-complex branch only, append the metadata label only when that exact label is absent from the displayed text. Source units, assets, translations and approval evidence are not modified. Asset candidate renderer functions and their runtime fingerprints are unchanged.

Validation: existing test_workflow.py render/markdown tests running; result to be appended. No new environment or global configuration changes.

Validation completed: pytest test_workflow.py -k 'render or markdown' passed (exit 0). Only a pytest cache permission warning was emitted; no test failures.
