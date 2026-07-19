from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rapidfuzz.fuzz import ratio

from littrans.extractor import parse_page_spec
from littrans.models import ProjectStatus, SourceUnit, TranslationRecord, UnitKind
from littrans.project import promote_status, translation_map
from littrans.storage import load_project, project_write_lock, read_jsonl, write_json, write_jsonl


def _comparable(text: str) -> str:
    return re.sub(r"\W+", "", text.casefold(), flags=re.UNICODE)


def migrate_translations(
    source_root: Path,
    target_root: Path,
    page_spec: str = "all",
    minimum_score: float = 88.0,
) -> dict[str, Any]:
    """Copy draft translations across an explicit re-extraction without claiming approval."""
    target_config = load_project(target_root)
    source_config = load_project(source_root)
    if source_config.source_sha256 != target_config.source_sha256:
        raise ValueError("Translation migration requires the exact same source PDF hash")
    pages = set(parse_page_spec(page_spec, target_config.source_pages))
    source_units = read_jsonl(source_root / "derived" / "units.jsonl", SourceUnit)
    target_units = [
        unit
        for unit in read_jsonl(target_root / "derived" / "units.jsonl", SourceUnit)
        if unit.page in pages and unit.translatable
    ]
    source_translations = translation_map(source_root)
    candidates = [
        unit
        for unit in source_units
        if unit.page in pages and unit.unit_id in source_translations
    ]
    migrated: list[TranslationRecord] = []
    matches: list[dict[str, Any]] = []
    unmatched: list[str] = []
    used: set[str] = set()
    for target in target_units:
        if target.kind is UnitKind.TABLE:
            unmatched.append(target.unit_id)
            continue
        pool = [
            unit
            for unit in candidates
            if unit.page == target.page
            and unit.unit_id not in used
            and (
                unit.kind is target.kind
                or {unit.kind, target.kind} <= {UnitKind.PARAGRAPH, UnitKind.NOTE}
            )
        ]
        scored = [
            (ratio(_comparable(target.source_text), _comparable(unit.source_text)), unit)
            for unit in pool
        ]
        if not scored:
            unmatched.append(target.unit_id)
            continue
        score, source = max(scored, key=lambda item: item[0])
        if score < minimum_score:
            unmatched.append(target.unit_id)
            continue
        old = source_translations[source.unit_id]
        migrated.append(
            old.model_copy(
                update={
                    "unit_id": target.unit_id,
                    "source_hash": target.source_hash,
                    "revision": 1,
                    "status": ProjectStatus.DRAFT,
                    "target_table": None,
                }
            )
        )
        used.add(source.unit_id)
        matches.append(
            {
                "target_unit_id": target.unit_id,
                "source_unit_id": source.unit_id,
                "score": round(score, 2),
            }
        )
    with project_write_lock(target_root):
        write_jsonl(target_root / "translations" / "current.jsonl", migrated)
        write_jsonl(target_root / "translations" / "history.jsonl", migrated)
        promote_status(target_root, ProjectStatus.DRAFT)
    report = {
        "source_project": str(source_root),
        "target_project": str(target_root),
        "source_sha256": target_config.source_sha256,
        "migrated": len(migrated),
        "unmatched": unmatched,
        "matches": matches,
        "warning": "All migrated records are drafts; rerun QA and independent audit.",
    }
    write_json(target_root / "translations" / "migration-report.json", report)
    return report
