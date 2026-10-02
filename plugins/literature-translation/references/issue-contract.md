# Audit issue guidance

Use the [audit issue contract](cli-reference.md#audit-issue-contract) for JSONL fields,
enumerations, empty results and issue-ID handling.

Not translation defects: renderer-owned literal labels and heading markers absent from `target_text`, an omitted admonition shell, `{{asset:ID}}` placeholders in place of formulas, and `language_present=false` with notes on a notation-only image. Translatable label parts still require translations under the [translation contract](cli-reference.md#translation-contract).

Do not report a stylistic preference as an accuracy problem. Do not use vague explanations such as "unnatural" without identifying the defect.
