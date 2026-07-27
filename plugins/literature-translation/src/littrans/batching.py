from __future__ import annotations

from pathlib import Path

import yaml

from littrans.extractor import parse_page_spec
from littrans.models import (
    BatchManifest,
    ProjectStatus,
    RenderPolicy,
    SourceUnit,
    TranslationRecord,
)
from littrans.project import load_profile, load_terms, promote_status, translation_map
from littrans.semantics import fenced_code, table_to_markdown
from littrans.storage import (
    load_project,
    project_write_lock,
    read_jsonl,
    read_yaml,
    write_json,
    write_jsonl,
    write_yaml,
)
from littrans.verification import require_verified_extraction


def _word_count(text: str) -> int:
    return len(text.split())


def _unit_markdown(unit: SourceUnit, project_root: Path) -> str:
    marker = (
        f"<!-- unit: {unit.unit_id} | page: {unit.page} | kind: {unit.kind} | "
        f"translatable: {str(unit.translatable).lower()} | source_hash: {unit.source_hash} | "
        f"verified: {unit.verification_status} | continues: {str(unit.continues_from_previous).lower()} -->"
    )
    if unit.kind == "code":
        body = fenced_code(unit.source_text, unit.code_language)
    elif unit.kind == "equation":
        number = f" \\tag{{{unit.equation_number}}}" if unit.equation_number else ""
        body = f"$$\n{unit.latex or unit.source_text}{number}\n$$"
    elif unit.kind == "table" and unit.table:
        body = table_to_markdown(unit.table)
    elif unit.kind == "note":
        body = "> [!NOTE]\n" + "\n".join(
            f"> {line}" for line in unit.source_text.splitlines()
        )
    elif unit.asset_refs:
        refs = []
        for asset in unit.asset_refs:
            path = Path(asset.path)
            relative = Path("..") / ".." / path
            refs.append(f"![{unit.kind} on PDF page {unit.page}]({relative.as_posix()})")
        body = "\n".join(refs)
        if unit.source_text:
            body += f"\n\n{unit.source_text}"
    else:
        body = unit.source_markdown or unit.source_text
    return f"{marker}\n{body}\n"


def _context_text(
    root: Path, units: list[SourceUnit], before: SourceUnit | None, after: SourceUnit | None
) -> str:
    brief = (root / "context" / "document-brief.md").read_text(encoding="utf-8")
    style = (root / "context" / "style-guide.md").read_text(encoding="utf-8")
    terms = load_terms(root)
    adjacent = []
    if before:
        adjacent.append(f"Previous unit ({before.unit_id}):\n{before.source_text}")
    if after:
        adjacent.append(f"Next unit ({after.unit_id}):\n{after.source_text}")
    term_text = yaml.safe_dump({"approved_terms": terms}, allow_unicode=True, sort_keys=False)
    approved_memory = [
        record
        for record in translation_map(root).values()
        if record.status in {ProjectStatus.MACHINE_REVIEWED, ProjectStatus.HUMAN_APPROVED}
    ][-8:]
    memory_text = (
        "\n".join(f"- {record.unit_id}: {record.target_text}" for record in approved_memory)
        or "None yet."
    )
    return (
        f"{brief.rstrip()}\n\n{style.rstrip()}\n\n# Approved terminology\n\n"
        f"```yaml\n{term_text}```\n\n# Approved translation memory\n\n{memory_text}"
        f"\n\n# Adjacent source context\n\n" + "\n\n".join(adjacent) + "\n"
    )


def create_batches(
    root: Path,
    page_spec: str,
    max_words: int | None = None,
    prefix: str | None = None,
    untranslated_only: bool = False,
) -> list[BatchManifest]:
    config = load_project(root)
    if max_words is None:
        profile = load_profile(config.profile)
        batch_settings = profile.get("batch", {})
        max_words = int(batch_settings.get("max_source_words", 900))
    if max_words < 100:
        raise ValueError("max_words must be at least 100")
    pages = set(parse_page_spec(page_spec, config.source_pages))
    all_units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    selected = [
        unit
        for unit in all_units
        if unit.page in pages and unit.render_policy is RenderPolicy.INCLUDE
    ]
    if untranslated_only:
        translated_ids = set(translation_map(root))
        selected = [
            unit for unit in selected if unit.translatable and unit.unit_id not in translated_ids
        ]
    if not selected:
        raise ValueError(
            "No matching untranslated units remain"
            if untranslated_only
            else "No extracted units match the requested pages"
        )

    require_verified_extraction(root, pages)

    groups: list[list[SourceUnit]] = []
    current: list[SourceUnit] = []
    words = 0
    for unit in selected:
        unit_words = _word_count(unit.source_text) if unit.translatable else 0
        heading_boundary = unit.kind == "heading" and current and words >= max_words * 0.55
        page_gap = bool(current and unit.page - current[-1].page > 1)
        word_boundary = words + unit_words > max_words and unit.page != current[-1].page
        hard_boundary = words + unit_words > max_words * 1.5
        if current and (word_boundary or hard_boundary or heading_boundary or page_gap):
            groups.append(current)
            current, words = [], 0
        current.append(unit)
        words += unit_words
    if current:
        groups.append(current)

    base_prefix = prefix or f"p{min(pages):04}-p{max(pages):04}"
    manifests: list[BatchManifest] = []
    for index, group in enumerate(groups, 1):
        batch_id = f"{base_prefix}-b{index:03}"
        batch_dir = root / "batches" / batch_id
        if batch_dir.exists():
            raise FileExistsError(f"Batch already exists: {batch_id}")
        batch_dir.mkdir(parents=True)
        manifest = BatchManifest(
            batch_id=batch_id,
            project_id=config.project_id,
            pages=sorted({unit.page for unit in group}),
            unit_ids=[unit.unit_id for unit in group],
            translatable_unit_ids=[unit.unit_id for unit in group if unit.translatable],
            source_words=sum(_word_count(unit.source_text) for unit in group if unit.translatable),
        )
        start_index = all_units.index(group[0])
        end_index = all_units.index(group[-1])
        before = all_units[start_index - 1] if start_index > 0 else None
        after = all_units[end_index + 1] if end_index + 1 < len(all_units) else None
        write_yaml(batch_dir / "manifest.yaml", manifest.model_dump(mode="json"))
        (batch_dir / "source.md").write_text(
            "\n".join(_unit_markdown(unit, root) for unit in group), encoding="utf-8"
        )
        (batch_dir / "context.md").write_text(
            _context_text(root, group, before, after), encoding="utf-8"
        )
        write_json(batch_dir / "output-schema.json", _translation_output_schema())
        manifests.append(manifest)
    promote_status(root, ProjectStatus.PREPARED)
    return manifests


def load_manifest(root: Path, batch_id: str) -> BatchManifest:
    return BatchManifest.model_validate(read_yaml(root / "batches" / batch_id / "manifest.yaml"))


def show_batch(root: Path, batch_id: str) -> dict[str, object]:
    manifest = load_manifest(root, batch_id)
    translation = root / "batches" / batch_id / "translation.jsonl"
    return {
        **manifest.model_dump(mode="json"),
        "translation_file": str(translation),
        "translation_exists": translation.exists(),
    }


def refresh_batch(root: Path, batch_id: str) -> BatchManifest:
    """Refresh a batch after safe structural overrides while preserving its identity."""
    manifest = load_manifest(root, batch_id)
    all_units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    unit_map = {unit.unit_id: unit for unit in all_units}
    missing = [unit_id for unit_id in manifest.unit_ids if unit_id not in unit_map]
    if missing:
        raise ValueError(f"Batch references removed units: {missing}")
    group = [
        unit_map[unit_id]
        for unit_id in manifest.unit_ids
        if unit_map[unit_id].render_policy is RenderPolicy.INCLUDE
    ]
    if not group:
        raise ValueError("Batch contains no renderable units after applying structural overrides")
    previous_scope = set(manifest.translatable_unit_ids)
    refreshed_scope = [
        unit.unit_id for unit in group if unit.translatable and unit.unit_id in previous_scope
    ]
    revised = manifest.model_copy(
        update={
            "pages": sorted({unit.page for unit in group}),
            "unit_ids": [unit.unit_id for unit in group],
            "translatable_unit_ids": refreshed_scope,
            "source_words": sum(
                _word_count(unit.source_text) for unit in group if unit.unit_id in refreshed_scope
            ),
        }
    )
    batch_dir = root / "batches" / batch_id
    start_index = all_units.index(group[0])
    end_index = all_units.index(group[-1])
    before = all_units[start_index - 1] if start_index > 0 else None
    after = all_units[end_index + 1] if end_index + 1 < len(all_units) else None
    with project_write_lock(root):
        write_yaml(batch_dir / "manifest.yaml", revised.model_dump(mode="json"))
        (batch_dir / "source.md").write_text(
            "\n".join(_unit_markdown(unit, root) for unit in group), encoding="utf-8"
        )
        (batch_dir / "context.md").write_text(
            _context_text(root, group, before, after), encoding="utf-8"
        )
        allowed = set(revised.translatable_unit_ids)
        current = translation_map(root)
        removed = {
            unit_id
            for unit_id in manifest.translatable_unit_ids
            if unit_id not in allowed and unit_id in current
        }
        if removed:
            write_jsonl(
                root / "translations" / "current.jsonl",
                (record for unit_id, record in current.items() if unit_id not in removed),
            )
        translation_path = batch_dir / "translation.jsonl"
        if translation_path.exists():
            records = read_jsonl(translation_path, TranslationRecord)
            write_jsonl(
                translation_path, (record for record in records if record.unit_id in allowed)
            )
    return revised


def _translation_output_schema() -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["unit_id", "target_text", "source_hash"],
        "additionalProperties": False,
        "properties": {
            "unit_id": {"type": "string"},
            "target_text": {"type": "string"},
            "target_table": {
                "type": ["object", "null"],
                "properties": {
                    "rows": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                    },
                    "header_rows": {"type": "integer", "minimum": 0},
                    "column_count": {"type": "integer", "minimum": 1},
                },
                "required": ["rows", "column_count"],
                "additionalProperties": False,
            },
            "figure_labels": {"type": "array"},
            "source_hash": {"type": "string"},
            "reader_note": {
                "type": ["object", "null"],
                "properties": {
                    "text": {"type": "string"},
                    "sources": {
                        "type": "array",
                        "items": {"type": "string", "format": "uri"},
                    },
                    "accessed_at": {"type": ["string", "null"]},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            "term_proposals": {"type": "array"},
            "uncertainties": {"type": "array", "items": {"type": "string"}},
        },
    }
