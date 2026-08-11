from __future__ import annotations

from pathlib import Path

from littrans.batching import batch_directory, load_manifest
from littrans.evidence import (
    effective_figure_labels,
    record_audit_invalidation,
    translations_semantically_equal,
)
from littrans.models import ProjectStatus, SourceUnit, TranslationRecord, utc_now
from littrans.project import promote_status, translation_map
from littrans.storage import (
    append_jsonl,
    project_write_lock,
    read_jsonl,
    require_current_project_schema,
    write_jsonl,
)


def submit_translation(root: Path, batch_id: str, input_path: Path) -> list[TranslationRecord]:
    require_current_project_schema(root, "Translation submission")
    manifest = load_manifest(root, batch_id)
    submitted = read_jsonl(input_path, TranslationRecord)
    expected = set(manifest.translatable_unit_ids)
    supplied = [record.unit_id for record in submitted]
    duplicates = {unit_id for unit_id in supplied if supplied.count(unit_id) > 1}
    if duplicates:
        raise ValueError(f"Duplicate translation unit IDs: {sorted(duplicates)}")
    if set(supplied) != expected:
        missing = sorted(expected - set(supplied))
        extra = sorted(set(supplied) - expected)
        raise ValueError(f"Translation coverage mismatch; missing={missing}, extra={extra}")

    units = {
        unit.unit_id: unit for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    }
    with project_write_lock(root):
        current = translation_map(root)
        normalized: list[TranslationRecord] = []
        changed: list[TranslationRecord] = []
        rebound: list[TranslationRecord] = []
        for record in submitted:
            unit = units[record.unit_id]
            if record.source_hash != unit.source_hash:
                raise ValueError(f"Source hash mismatch for {record.unit_id}")
            effective_figure_labels(unit, record)
            prior = current.get(record.unit_id)
            if prior is not None and translations_semantically_equal(
                unit, prior, record
            ):
                if prior.source_hash != record.source_hash:
                    binding_update = prior.model_copy(
                        update={
                            "source_hash": record.source_hash,
                            "status": ProjectStatus.REVISED,
                            "updated_at": utc_now(),
                        }
                    )
                    normalized.append(binding_update)
                    rebound.append(binding_update)
                else:
                    normalized.append(prior)
                continue
            revision = prior.revision + 1 if prior else 1
            status = ProjectStatus.REVISED if prior else ProjectStatus.DRAFT
            revised = record.model_copy(
                update={"revision": revision, "status": status, "updated_at": utc_now()}
            )
            normalized.append(revised)
            changed.append(revised)

        if not changed and not rebound:
            batch_path = batch_directory(root, batch_id) / "translation.jsonl"
            if input_path.resolve() == batch_path.resolve():
                batch_records = [
                    current[unit_id] for unit_id in manifest.translatable_unit_ids
                ]
                write_jsonl(batch_path, batch_records)
            return normalized

        current.update(
            {record.unit_id: record for record in [*changed, *rebound]}
        )
        write_jsonl(root / "translations" / "current.jsonl", current.values())
        if changed:
            append_jsonl(root / "translations" / "history.jsonl", changed)
        batch_records = [current[unit_id] for unit_id in manifest.translatable_unit_ids]
        write_jsonl(batch_directory(root, batch_id) / "translation.jsonl", batch_records)
        evidence_changes = [*changed, *rebound]
        if evidence_changes:
            record_audit_invalidation(
                root, batch_id, (record.unit_id for record in evidence_changes)
            )
            promote_status(
                root,
                ProjectStatus.REVISED
                if rebound or any(r.revision > 1 for r in changed)
                else ProjectStatus.DRAFT,
            )
    return normalized
