from __future__ import annotations

from pathlib import Path

from littrans.batching import load_manifest
from littrans.models import ProjectStatus, SourceUnit, TranslationRecord
from littrans.project import promote_status, translation_map
from littrans.storage import append_jsonl, project_write_lock, read_jsonl, write_jsonl


def submit_translation(root: Path, batch_id: str, input_path: Path) -> list[TranslationRecord]:
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
        for record in submitted:
            unit = units[record.unit_id]
            if record.source_hash != unit.source_hash:
                raise ValueError(f"Source hash mismatch for {record.unit_id}")
            prior = current.get(record.unit_id)
            revision = prior.revision + 1 if prior else 1
            status = ProjectStatus.REVISED if prior else ProjectStatus.DRAFT
            normalized.append(record.model_copy(update={"revision": revision, "status": status}))

        current.update({record.unit_id: record for record in normalized})
        write_jsonl(root / "translations" / "current.jsonl", current.values())
        append_jsonl(root / "translations" / "history.jsonl", normalized)
        write_jsonl(root / "batches" / batch_id / "translation.jsonl", normalized)
        promote_status(
            root,
            ProjectStatus.REVISED
            if any(r.revision > 1 for r in normalized)
            else ProjectStatus.DRAFT,
        )
    return normalized
