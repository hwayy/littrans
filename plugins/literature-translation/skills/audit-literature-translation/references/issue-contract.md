# Audit issue contract

Each JSONL record must contain:

```json
{
  "issue_id": "batch-r001",
  "batch_id": "p0001-p0010-b001",
  "unit_id": "p0003-u004-abcd1234",
  "severity": "major",
  "type": "meaning",
  "source_span": "exact source phrase",
  "target_span": "exact translated phrase",
  "explanation": "Why this changes or obscures the source meaning.",
  "suggested_revision": "A focused correction.",
  "confidence": 0.95,
  "reviewer": "fidelity-reviewer",
  "status": "open"
}
```

Severity:

- `blocker`: unusable or unsafe output, extensive missing content, corrupted structure.
- `major`: material mistranslation, omission, addition, technical error, or broken reference.
- `minor`: localized accuracy, terminology, or clarity defect.
- `suggestion`: optional polish that does not change correctness.

Type must be one of `meaning`, `omission`, `addition`, `terminology`, `technical`, `style`, `reference`, `number-unit`, or `format`.

Do not report a stylistic preference as an accuracy problem. Do not use vague explanations such as "unnatural" without identifying the defect.

