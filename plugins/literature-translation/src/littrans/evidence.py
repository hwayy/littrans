from __future__ import annotations

import json
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from littrans.models import (
    FigureLabel,
    ProjectStatus,
    SourceUnit,
    TranslationRecord,
    UnitKind,
    utc_now,
)
from littrans.project import load_terms, translation_map
from littrans.semantics import normalize_zh_caption
from littrans.storage import (
    load_project,
    read_json,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_json,
)

TRANSLATION_PAYLOAD_FIELDS = {
    "target_text",
    "target_table",
    "figure_labels",
    "reader_note",
    "term_proposals",
    "uncertainties",
}

SOURCE_SEMANTIC_FIELDS = {
    "kind",
    "page",
    "bbox",
    "source_text",
    "source_hash",
    "source_markdown",
    "parent_id",
    "sidebar_id",
    "sidebar_role",
    "callout_kind",
    "translatable",
    "render_policy",
    "protected_tokens",
    "asset_refs",
    "fragments",
    "latex",
    "equation_number",
    "math_status",
    "code_language",
    "table",
    "continues_from_previous",
    "continued_to_next",
    "figure_labels",
    "visual_text_status",
    "verification_status",
}

STRUCTURE_FIELDS = {
    "kind",
    "page",
    "bbox",
    "parent_id",
    "sidebar_id",
    "sidebar_role",
    "callout_kind",
    "translatable",
    "render_policy",
    "asset_refs",
    "fragments",
    "code_language",
    "continues_from_previous",
    "continued_to_next",
}


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def translation_payload(record: TranslationRecord) -> dict[str, Any]:
    """Return stored translation content, excluding workflow-only metadata."""
    return record.model_dump(
        mode="json", include=TRANSLATION_PAYLOAD_FIELDS, exclude_none=True
    )


def effective_figure_labels(
    unit: SourceUnit, record: TranslationRecord | None
) -> list[FigureLabel]:
    """Return the renderer-visible labels after validating any record override."""
    if record is None or not record.figure_labels:
        return unit.figure_labels
    expected = [label.source for label in unit.figure_labels]
    supplied = [label.source for label in record.figure_labels]
    if unit.kind is not UnitKind.FIGURE or supplied != expected:
        raise ValueError(
            f"Figure label mapping mismatch for {unit.unit_id}; "
            f"expected={expected}, supplied={supplied}"
        )
    return record.figure_labels


def effective_translation_payload(
    unit: SourceUnit, record: TranslationRecord
) -> dict[str, Any]:
    """Return renderer-aware semantic content for one source unit."""
    payload = translation_payload(record)
    try:
        rendered_figure_labels = effective_figure_labels(unit, record)
    except ValueError:
        # Keep malformed legacy records fingerprintable so deterministic QA can
        # report the mapping error instead of failing before it writes a report.
        rendered_figure_labels = record.figure_labels
    payload["figure_labels"] = [
        label.model_dump(mode="json", exclude_none=True)
        for label in rendered_figure_labels
    ]
    if unit.kind is UnitKind.CAPTION and "target_text" in payload:
        payload["target_text"] = normalize_zh_caption(payload["target_text"])
    return payload


def translations_semantically_equal(
    unit: SourceUnit, left: TranslationRecord, right: TranslationRecord
) -> bool:
    return effective_translation_payload(unit, left) == effective_translation_payload(
        unit, right
    )


def source_unit_fingerprint(unit: SourceUnit) -> str:
    return sha256_text(
        _canonical(
            unit.model_dump(
                mode="json", include=SOURCE_SEMANTIC_FIELDS, exclude_none=True
            )
        )
    )


def structure_unit_fingerprint(unit: SourceUnit) -> str:
    return sha256_text(
        _canonical(
            unit.model_dump(mode="json", include=STRUCTURE_FIELDS, exclude_none=True)
        )
    )


def translation_unit_fingerprint(
    unit: SourceUnit, record: TranslationRecord | None
) -> str:
    payload = {
        "source": source_unit_fingerprint(unit),
        "translation": (
            effective_translation_payload(unit, record)
            if record is not None
            else None
        ),
    }
    return sha256_text(_canonical(payload))


def project_units(root: Path) -> list[SourceUnit]:
    return read_jsonl(root / "derived" / "units.jsonl", SourceUnit)


def batch_unit_fingerprints(root: Path, batch_id: str) -> dict[str, str]:
    from littrans.batching import load_manifest

    manifest = load_manifest(root, batch_id)
    units = {unit.unit_id: unit for unit in project_units(root)}
    translations = translation_map(root)
    return {
        unit_id: translation_unit_fingerprint(units[unit_id], translations.get(unit_id))
        for unit_id in manifest.unit_ids
    }


def batch_source_fingerprint(root: Path, batch_id: str) -> str:
    from littrans.batching import load_manifest

    manifest = load_manifest(root, batch_id)
    units = {unit.unit_id: unit for unit in project_units(root)}
    return sha256_text(
        "\n".join(
            f"{unit_id}:{source_unit_fingerprint(units[unit_id])}"
            for unit_id in manifest.unit_ids
        )
    )


def batch_structure_fingerprint(root: Path, batch_id: str) -> str:
    from littrans.batching import load_manifest

    manifest = load_manifest(root, batch_id)
    units = {unit.unit_id: unit for unit in project_units(root)}
    return sha256_text(
        "\n".join(
            f"{unit_id}:{structure_unit_fingerprint(units[unit_id])}"
            for unit_id in manifest.unit_ids
        )
    )


def changed_units(
    current: dict[str, str], previous: dict[str, str]
) -> set[str]:
    return {
        unit_id
        for unit_id in set(current) | set(previous)
        if current.get(unit_id) != previous.get(unit_id)
    }


def dependency_closure(
    root: Path, batch_ids: Iterable[str], changed: Iterable[str]
) -> list[str]:
    """Expand edits to local semantic dependencies and batch seams."""
    from littrans.batching import load_manifest

    units = project_units(root)
    positions = {unit.unit_id: index for index, unit in enumerate(units)}
    selected = {unit_id for unit_id in changed if unit_id in positions}
    if not selected:
        return []

    # Immediate context is always reviewed for a changed unit.
    for unit_id in list(selected):
        index = positions[unit_id]
        if index:
            selected.add(units[index - 1].unit_id)
        if index + 1 < len(units):
            selected.add(units[index + 1].unit_id)

    # Continued structures and sidebars may pull one another into the closure.
    while True:
        previous_size = len(selected)
        for unit_id in list(selected):
            index = positions[unit_id]
            left = index
            while left > 0 and (
                units[left].continues_from_previous
                or units[left - 1].continued_to_next
            ):
                left -= 1
                selected.add(units[left].unit_id)
            right = index
            while right + 1 < len(units) and (
                units[right].continued_to_next
                or units[right + 1].continues_from_previous
            ):
                right += 1
                selected.add(units[right].unit_id)
        sidebar_ids = {
            units[positions[unit_id]].sidebar_id
            for unit_id in selected
            if units[positions[unit_id]].sidebar_id
        }
        selected.update(
            unit.unit_id for unit in units if unit.sidebar_id in sidebar_ids
        )
        if len(selected) == previous_size:
            break

    # Include both sides only for batch seams reached by the dependency closure.
    for batch_id in batch_ids:
        manifest = load_manifest(root, batch_id)
        for seam_id, outside_offset in (
            (manifest.unit_ids[0], -1),
            (manifest.unit_ids[-1], 1),
        ):
            if seam_id not in selected:
                continue
            index = positions[seam_id]
            selected.add(seam_id)
            outside_index = index + outside_offset
            if 0 <= outside_index < len(units):
                selected.add(units[outside_index].unit_id)
    return [unit.unit_id for unit in units if unit.unit_id in selected]


def record_audit_invalidation(
    root: Path, batch_id: str, changed_unit_ids: Iterable[str]
) -> dict[str, list[str]]:
    """Invalidate all lens coverage for the edit dependency closure."""
    closure = set(dependency_closure(root, [batch_id], changed_unit_ids))
    invalidated: dict[str, list[str]] = {}
    timestamp = utc_now()
    batch_root = root / "batches"
    for path in batch_root.iterdir():
        if not path.is_dir() or not (path / "manifest.yaml").is_file():
            continue
        from littrans.batching import load_manifest

        manifest = load_manifest(root, path.name)
        affected = [unit_id for unit_id in manifest.unit_ids if unit_id in closure]
        if not affected:
            continue
        invalidation_path = (
            root / "evidence" / "audits" / f"{manifest.batch_id}.invalidations.json"
        )
        payload = read_json(invalidation_path) if invalidation_path.is_file() else {}
        entries = payload.get("units", {})
        if not isinstance(entries, dict):
            entries = {}
        for unit_id in affected:
            entries[unit_id] = timestamp
        write_json(
            invalidation_path,
            {"batch_id": manifest.batch_id, "units": entries, "updated_at": timestamp},
        )
        invalidated[manifest.batch_id] = affected
    return invalidated


def page_evidence_units(page: int, units: list[SourceUnit]) -> list[SourceUnit]:
    """Return the source units that determine one page's verification receipt."""
    selected_indices = {index for index, unit in enumerate(units) if unit.page == page}
    while True:
        previous_size = len(selected_indices)
        sidebar_ids = {
            units[index].sidebar_id
            for index in selected_indices
            if units[index].sidebar_id
        }
        selected_indices.update(
            index for index, unit in enumerate(units) if unit.sidebar_id in sidebar_ids
        )
        for index in list(selected_indices):
            left = index
            while left > 0 and (
                units[left].continues_from_previous
                or units[left - 1].continued_to_next
            ):
                left -= 1
                selected_indices.add(left)
            right = index
            while right + 1 < len(units) and (
                units[right].continued_to_next
                or units[right + 1].continues_from_previous
            ):
                right += 1
                selected_indices.add(right)
        if len(selected_indices) == previous_size:
            break
    return [units[index] for index in sorted(selected_indices)]


def page_evidence_fingerprints(
    root: Path, page: int, units: list[SourceUnit]
) -> tuple[str, str]:
    page_units = page_evidence_units(page, units)
    unit_fingerprint = sha256_text(
        "\n".join(
            f"{unit.unit_id}:{source_unit_fingerprint(unit)}" for unit in page_units
        )
    )
    assets: list[str] = []
    for unit in page_units:
        for asset in unit.asset_refs:
            path = root / asset.path
            digest = sha256_file(path) if path.is_file() else "missing"
            assets.append(f"{asset.path}:{digest}")
    return unit_fingerprint, sha256_text("\n".join(sorted(assets)))


def source_representation_text(unit: SourceUnit) -> str:
    """Return every source representation whose content is translated or reviewed."""
    parts = [unit.source_text]
    if unit.source_markdown and unit.source_markdown != unit.source_text:
        parts.append(unit.source_markdown)
    if unit.table:
        parts.extend(cell for row in unit.table.rows for cell in row)
    parts.extend(label.source for label in unit.figure_labels)
    return "\n".join(part for part in parts if part)


def relevant_terms(root: Path, units: Iterable[SourceUnit]) -> list[dict[str, Any]]:
    selected = list(units)
    source = "\n".join(
        source_representation_text(unit) for unit in selected
    ).casefold()
    pages = {unit.page for unit in selected}
    parents = {unit.parent_id for unit in selected if unit.parent_id}
    matches: list[dict[str, Any]] = []
    for term in load_terms(root):
        source_term = str(term.get("source", "")).strip()
        scope = str(term.get("scope", "document"))
        in_scope = (
            scope == "document"
            or scope in parents
            or any(scope == f"page:{page}" for page in pages)
        )
        if source_term and in_scope and source_term.casefold() in source:
            matches.append(term)
    return matches


def audit_context_text(root: Path, units: Iterable[SourceUnit]) -> str:
    """Return the exact shared instructions and terminology shown to auditors."""
    selected = list(units)
    brief = (root / "context" / "document-brief.md").read_text(
        encoding="utf-8"
    ).strip()
    style = (root / "context" / "style-guide.md").read_text(
        encoding="utf-8"
    ).strip()
    terms = yaml.safe_dump(
        {"approved_terms": relevant_terms(root, selected)},
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    return (
        f"# Document brief\n\n{brief}\n\n# Translation style\n\n{style}\n\n"
        f"# Relevant approved terminology\n\n```yaml\n{terms}\n```\n"
    )


def audit_context_fingerprint(root: Path, units: Iterable[SourceUnit]) -> str:
    return sha256_text(audit_context_text(root, units))


def translation_memory(
    root: Path, current_unit_ids: Iterable[str], limit: int = 6
) -> list[dict[str, str]]:
    current_ids = set(current_unit_ids)
    config_external = load_project(root).external_review
    units_path = root / "derived" / "units.jsonl"
    translations_path = root / "translations" / "current.jsonl"
    unit_stat = units_path.stat()
    translation_stat = translations_path.stat() if translations_path.is_file() else None
    units, candidates = _memory_index(
        str(root.resolve()),
        unit_stat.st_mtime_ns,
        unit_stat.st_size,
        translation_stat.st_mtime_ns if translation_stat else 0,
        translation_stat.st_size if translation_stat else 0,
        bool(config_external and config_external.enabled),
    )
    positions = {unit_id: index for index, (unit_id, _, _) in enumerate(units)}
    source_by_id = {unit_id: source for unit_id, source, _ in units}
    current_source = " ".join(
        source_by_id[unit_id] for unit_id in current_ids if unit_id in source_by_id
    )
    current_tokens = _memory_tokens(current_source)
    adjacent_ids: set[str] = set()
    for unit_id in current_ids:
        index = positions.get(unit_id)
        if index is None:
            continue
        if index:
            adjacent_ids.add(units[index - 1][0])
        if index + 1 < len(units):
            adjacent_ids.add(units[index + 1][0])

    ranked: list[tuple[float, int, str, str, str]] = []
    for unit_id, source, target, tokens in candidates:
        if unit_id in current_ids:
            continue
        adjacent = 1 if unit_id in adjacent_ids else 0
        candidate_tokens = set(tokens)
        union = current_tokens | candidate_tokens
        similarity = len(current_tokens & candidate_tokens) / len(union) if union else 0.0
        ranked.append((similarity + adjacent * 2.0, adjacent, unit_id, source, target))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [
        {
            "unit_id": unit_id,
            "source": source,
            "target": target,
        }
        for _, _, unit_id, source, target in ranked[:limit]
    ]


def _memory_tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,}|[\u3400-\u9fff]{2,}", text.casefold()))


@lru_cache(maxsize=8)
def _memory_index(
    root_text: str,
    units_mtime_ns: int,
    units_size: int,
    translations_mtime_ns: int,
    translations_size: int,
    external_enabled: bool,
) -> tuple[
    tuple[tuple[str, str, str], ...],
    tuple[tuple[str, str, str, tuple[str, ...]], ...],
]:
    del units_mtime_ns, units_size, translations_mtime_ns, translations_size
    root = Path(root_text)
    units = project_units(root)
    translations = translation_map(root)
    unit_entries = tuple(
        (unit.unit_id, unit.source_text, unit.source_hash) for unit in units
    )
    unit_map = {unit.unit_id: unit for unit in units}
    allowed = (
        {ProjectStatus.EXTERNAL_REVIEWED, ProjectStatus.HUMAN_APPROVED}
        if external_enabled
        else {
            ProjectStatus.MACHINE_REVIEWED,
            ProjectStatus.EXTERNAL_REVIEWED,
            ProjectStatus.HUMAN_APPROVED,
        }
    )
    candidates = tuple(
        (
            unit_id,
            unit_map[unit_id].source_text,
            record.target_text,
            tuple(sorted(_memory_tokens(unit_map[unit_id].source_text))),
        )
        for unit_id, record in translations.items()
        if unit_id in unit_map
        and record.status in allowed
        and record.source_hash == unit_map[unit_id].source_hash
    )
    return unit_entries, candidates
