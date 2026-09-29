# Settings field ownership

Generated from the settings model. Run `config schema` for types and constraints.

| Field | Domain | Consumer | Change effect |
| --- | --- | --- | --- |
| `schema_version` | format | configuration loader | immutable format version |
| `preset.name` | provenance | initialization/reset | preset source; no runtime inheritance |
| `preset.version` | provenance | initialization/reset | preset source; no runtime inheritance |
| `preset.sha256` | provenance | initialization/reset | preset source; no runtime inheritance |
| `document.title` | document | packets/rendering/provenance | language changes invalidate translation evidence |
| `document.source_language` | document | packets/rendering/provenance | language changes invalidate translation evidence |
| `document.target_language` | document | packets/rendering/provenance | language changes invalidate translation evidence |
| `document.rights_status` | document | packets/rendering/provenance | language changes invalidate translation evidence |
| `batch.max_source_words` | batch | batch creation | future batches only |
| `batch.soft_max_assets` | batch | batch creation | future batches only |
| `batch.preserve_heading_boundaries` | batch | batch creation | future batches only |
| `outline_source` | source | source preparation/review | source receipts become stale |
| `agents.*.defaults.model` | dispatch | task creation/native agent generation | new tasks only |
| `agents.*.defaults.reasoning_effort` | dispatch | task creation/native agent generation | new tasks only |
| `agents.*.roles.*.model` | dispatch | task creation/native agent generation | new tasks only |
| `agents.*.roles.*.reasoning_effort` | dispatch | task creation/native agent generation | new tasks only |
| `agents.*.audit_lenses.*.model` | dispatch | task creation/native agent generation | new tasks only |
| `agents.*.audit_lenses.*.reasoning_effort` | dispatch | task creation/native agent generation | new tasks only |
| `agents.*.wave_size` | dispatch | task creation/native agent generation | new tasks only |
| `translation.code.translate_comments` | translation | submission/QA/audit/rendering | dependent QA/audits or presentation |
| `translation.code.translate_string_literals` | translation | submission/QA/audit/rendering | dependent QA/audits or presentation |
| `translation.code.detect_language` | source | source preparation | source receipts become stale |
| `translation.equations.inline` | presentation | asset selection/approval/rendering | output plus required transcription gate |
| `translation.equations.display` | presentation | asset selection/approval/rendering | output plus required transcription gate |
| `translation.figures.translate_caption` | translation | submission/QA/audit/rendering | dependent QA/audits or presentation |
| `translation.figures.internal_labels` | translation | submission/QA/audit/rendering | dependent QA/audits or presentation |
| `translation.tables.translation` | translation | submission/QA/audit/rendering | dependent QA/audits or presentation |
| `translation.tables.presentation` | presentation | asset selection/approval/rendering | output plus required transcription gate |
| `translation.tables.image_fallback_in_final` | presentation | asset selection/approval/rendering | output plus required transcription gate |
| `translation.reader_notes.allow_modernization` | translation | submission/QA/audit/rendering | dependent QA/audits or presentation |
| `translation.reader_notes.require_primary_https_sources` | translation | submission/QA/audit/rendering | dependent QA/audits or presentation |
| `translation.memory_minimum_status` | translation | submission/QA/audit/rendering | dependent QA/audits or presentation |
| `translation.prose_punctuation` | translation | submission/QA/audit/rendering | dependent QA/audits or presentation |
| `verification.block_unfinished_transcription` | verification | approval/final rendering | delivery requirements |
| `external_review.enabled` | external | external CLI/recheck gates | model/context changes stale external evidence |
| `external_review.reviewers.*.driver` | external | external CLI/recheck gates | model/context changes stale external evidence |
| `external_review.reviewers.*.model` | external | external CLI/recheck gates | model/context changes stale external evidence |
| `external_review.reviewers.*.model_identity` | external | external CLI/recheck gates | model/context changes stale external evidence |
| `external_review.reviewers.*.effort` | external | external CLI/recheck gates | model/context changes stale external evidence |
| `external_review.primary` | external | external CLI/recheck gates | model/context changes stale external evidence |
| `external_review.fallbacks` | external | external CLI/recheck gates | model/context changes stale external evidence |
| `external_review.recheck.confidence_below` | recheck | external status/adjudication | recompute required rechecks from saved findings |
| `external_review.recheck.severities` | recheck | external status/adjudication | recompute required rechecks from saved findings |
| `external_review.domain_expertise` | external | external CLI/recheck gates | model/context changes stale external evidence |
| `external_review.timeout_seconds` | execution | external subprocess | future invocations only |
