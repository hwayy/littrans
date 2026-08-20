from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from littrans.batching import batch_source_markdown, load_manifest
from littrans.evidence import (
    audit_context_text,
    dependency_closure,
    effective_figure_labels,
    equation_markdown,
    translation_memory,
    translation_unit_fingerprint,
    translations_semantically_equal,
)
from littrans.hosts import (
    LENS_REVIEWER_BATCH_MAX,
    WAVE_BATCH_SET_MAX,
    resolve_coordination_host,
    resolve_wave_limit,
)
from littrans.models import (
    AuditRun,
    BatchManifest,
    ExternalReviewRun,
    IssueStatus,
    ProjectStatus,
    QAReport,
    RenderPolicy,
    ReviewIssue,
    Severity,
    SourceUnit,
    TranslationRecord,
    UnitKind,
    WorkflowPacketManifest,
)
from littrans.project import translation_map
from littrans.quality import (
    REQUIRED_AUDIT_LENSES,
    _apply_review_import_locked,
    _prepare_review_import_locked,
    audit_coverage,
    audit_evidence_context_fingerprint,
    current_qa_context_fingerprint,
)
from littrans.semantics import normalize_zh_caption
from littrans.storage import (
    atomic_write_text,
    load_project,
    project_write_lock,
    read_json,
    read_jsonl,
    require_current_project_schema,
    restore_files,
    sha256_file,
    sha256_text,
    snapshot_files,
    write_json,
)


@dataclass(frozen=True)
class WorkflowSnapshot:
    """One-load, internally consistent view used by workflow coordination."""

    root: Path
    manifests: tuple[BatchManifest, ...]
    units: tuple[SourceUnit, ...]
    unit_map: dict[str, SourceUnit]
    translations: dict[str, TranslationRecord]
    qa_reports: dict[str, QAReport | None]
    issues: dict[str, list[ReviewIssue]]
    audit_runs: dict[str, list[AuditRun]]
    external_status: dict[str, dict[str, Any] | None]
    external_enabled: bool
    qa_context_fingerprint: str


def _translation_fingerprint_from_snapshot(
    snapshot: WorkflowSnapshot, manifest: BatchManifest
) -> str:
    fingerprints = {
        unit_id: translation_unit_fingerprint(
            snapshot.unit_map[unit_id], snapshot.translations.get(unit_id)
        )
        for unit_id in manifest.unit_ids
        if unit_id in snapshot.unit_map
    }
    return sha256_text(
        "\n".join(f"{unit_id}:{value}" for unit_id, value in fingerprints.items())
    )


def _load_workflow_snapshot(
    root: Path, external_batch_ids: set[str] | None = None
) -> WorkflowSnapshot:
    # Import lazily to avoid coupling the packet/review implementation at module
    # import time.  The derived external.json file is a convenience cache, not
    # authoritative evidence: rebuild each status from current runs and context.
    from littrans.external_review import external_review_status

    config = load_project(root)
    units = tuple(read_jsonl(root / "derived" / "units.jsonl", SourceUnit))
    unit_map = {unit.unit_id: unit for unit in units}
    positions = {unit.unit_id: index for index, unit in enumerate(units)}
    manifests = tuple(
        sorted(
            (
                load_manifest(root, path.name)
                for path in (root / "batches").iterdir()
                if path.is_dir() and (path / "manifest.yaml").is_file()
            ),
            key=lambda manifest: min(
                (positions.get(unit_id, 10**12) for unit_id in manifest.unit_ids),
                default=10**12,
            ),
        )
    )
    translations = translation_map(root)
    qa_reports: dict[str, QAReport | None] = {}
    issues: dict[str, list[ReviewIssue]] = {}
    audit_runs: dict[str, list[AuditRun]] = {}
    external_status: dict[str, dict[str, Any] | None] = {}
    for manifest in manifests:
        batch_id = manifest.batch_id
        qa_path = root / "qa" / f"{batch_id}.json"
        qa_reports[batch_id] = (
            QAReport.model_validate(read_json(qa_path)) if qa_path.is_file() else None
        )
        issues[batch_id] = read_jsonl(
            root / "reviews" / f"{batch_id}.issues.jsonl", ReviewIssue
        )
        audit_runs[batch_id] = read_jsonl(
            root / "evidence" / "audits" / f"{batch_id}.jsonl", AuditRun
        )
        external_status[batch_id] = (
            external_review_status(
                root,
                batch_id,
                include_reviewer_usage=False,
                current_fingerprint=sha256_text(
                    "\n".join(
                        f"{unit_id}:{translation_unit_fingerprint(unit_map[unit_id], translations.get(unit_id))}"
                        for unit_id in manifest.unit_ids
                        if unit_id in unit_map
                    )
                ),
                all_units=list(units),
                translations=translations,
            )
            if (
                config.external_review
                and config.external_review.enabled
                and (external_batch_ids is None or batch_id in external_batch_ids)
            )
            else None
        )
    return WorkflowSnapshot(
        root=root,
        manifests=manifests,
        units=units,
        unit_map=unit_map,
        translations=translations,
        qa_reports=qa_reports,
        issues=issues,
        audit_runs=audit_runs,
        external_status=external_status,
        external_enabled=bool(config.external_review and config.external_review.enabled),
        qa_context_fingerprint=current_qa_context_fingerprint(root),
    )


def _all_manifests(root: Path) -> list[BatchManifest]:
    units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    positions = {unit.unit_id: index for index, unit in enumerate(units)}
    manifests = [
        load_manifest(root, path.name)
        for path in (root / "batches").iterdir()
        if path.is_dir() and (path / "manifest.yaml").is_file()
    ]
    return sorted(
        manifests,
        key=lambda manifest: min(
            (positions.get(unit_id, 10**12) for unit_id in manifest.unit_ids),
            default=10**12,
        ),
    )


BATCH_SERIES_RE = re.compile(r"^(?P<series>.+)-b\d+$")


def _batch_series(batch_id: str | None) -> str | None:
    if batch_id is None:
        return None
    match = BATCH_SERIES_RE.fullmatch(batch_id)
    return match.group("series") if match else None


def _bounded_manifest_series(
    manifests: list[BatchManifest], start_at: str | None, through: str | None
) -> list[BatchManifest]:
    """Keep a resumed range on the lineage identified by its boundary IDs."""
    start_series = _batch_series(start_at)
    through_series = _batch_series(through)
    active_series = start_series or through_series
    if active_series is None or (
        start_series is not None
        and through_series is not None
        and start_series != through_series
    ):
        return manifests
    return [
        manifest
        for manifest in manifests
        if _batch_series(manifest.batch_id) == active_series
    ]


def _batch_stage(
    root: Path,
    batch_id: str,
    snapshot: WorkflowSnapshot | None = None,
    context_cache: dict[tuple[str, ...], tuple[str, dict[str, str]] | None]
    | None = None,
) -> str:
    snapshot = snapshot or _load_workflow_snapshot(root)
    manifest = next(
        (item for item in snapshot.manifests if item.batch_id == batch_id), None
    )
    if manifest is None:
        raise ValueError(f"Unknown batch ID: {batch_id}")
    translations = snapshot.translations
    if any(unit_id not in translations for unit_id in manifest.translatable_unit_ids):
        return "translate"
    qa_report = snapshot.qa_reports[batch_id]
    if not (
        qa_report
        and qa_report.passed
        and qa_report.translation_fingerprint
        == _translation_fingerprint_from_snapshot(snapshot, manifest)
        and qa_report.qa_context_fingerprint == snapshot.qa_context_fingerprint
    ):
        return "qa"
    if not audit_coverage(
        root,
        batch_id,
        manifest=manifest,
        all_units=snapshot.unit_map,
        translations=translations,
        runs=snapshot.audit_runs[batch_id],
        context_cache=context_cache,
    )["complete"]:
        return "audit"
    open_issues = [
        issue for issue in snapshot.issues[batch_id] if issue.status is IssueStatus.OPEN
    ]
    external_enabled = snapshot.external_enabled
    open_substantive = [
        issue
        for issue in open_issues
        if issue.severity is not Severity.SUGGESTION
    ]
    open_blocking = [
        issue
        for issue in open_issues
        if issue.severity in {Severity.BLOCKER, Severity.MAJOR}
    ]
    if open_blocking or (external_enabled and open_substantive):
        return "revise"
    allowed_machine = {
        ProjectStatus.MACHINE_REVIEWED,
        ProjectStatus.EXTERNAL_REVIEWED,
        ProjectStatus.HUMAN_APPROVED,
    }
    if any(
        translations[unit_id].status not in allowed_machine
        for unit_id in manifest.translatable_unit_ids
    ):
        return "machine-approve"
    if external_enabled:
        external = snapshot.external_status[batch_id]
        current_fingerprint = _translation_fingerprint_from_snapshot(snapshot, manifest)
        if not (
            external
            and external.get("translation_fingerprint") == current_fingerprint
            and external.get("verdict") == "accepted"
            and external.get("external_approvable") is True
            and not open_substantive
        ):
            return "external-review"
        if any(
            translations[unit_id].status
            not in {ProjectStatus.EXTERNAL_REVIEWED, ProjectStatus.HUMAN_APPROVED}
            for unit_id in manifest.translatable_unit_ids
        ):
            return "external-approve"
    return "complete"


def workflow_next(
    root: Path,
    limit: int | None = None,
    start_at: str | None = None,
    through: str | None = None,
    host: str | None = None,
) -> dict[str, Any]:
    require_current_project_schema(root, "Workflow coordination")
    resolved_host = resolve_coordination_host(host)
    resolved_limit = resolve_wave_limit(resolved_host, limit)
    external_batch_ids: set[str] | None = None
    if start_at is not None or through is not None:
        ordered = _bounded_manifest_series(
            _all_manifests(root), start_at, through
        )
        ordered_indexes = {
            manifest.batch_id: index for index, manifest in enumerate(ordered)
        }
        if (start_at is None or start_at in ordered_indexes) and (
            through is None or through in ordered_indexes
        ):
            external_lower = ordered_indexes[start_at] if start_at else 0
            external_upper = (
                ordered_indexes[through] if through else len(ordered) - 1
            )
            external_batch_ids = {
                manifest.batch_id
                for manifest in ordered[external_lower : external_upper + 1]
            }
    snapshot = _load_workflow_snapshot(root, external_batch_ids)
    manifests = list(snapshot.manifests)
    all_manifests = list(manifests)
    manifests = _bounded_manifest_series(manifests, start_at, through)
    units = list(snapshot.units)
    unit_map = snapshot.unit_map
    indexes = {manifest.batch_id: index for index, manifest in enumerate(manifests)}
    for label, batch_id in (("start-at", start_at), ("through", through)):
        if batch_id is not None and batch_id not in indexes:
            raise ValueError(f"workflow next --{label} references unknown batch: {batch_id}")
    lower = indexes[start_at] if start_at else 0
    upper = indexes[through] if through else len(manifests) - 1
    if lower > upper:
        raise ValueError("workflow next --start-at must not follow --through")
    manifest_unit_ids = {
        unit_id for manifest in all_manifests for unit_id in manifest.unit_ids
    }
    unbatched_units = sorted(
        unit.unit_id
        for unit in units
        if unit.render_policy is RenderPolicy.INCLUDE
        and unit.unit_id not in manifest_unit_ids
    )
    if unbatched_units:
        raise ValueError(
            "Workflow manifests do not cover current renderable source units; "
            "refresh or create batches before continuing: "
            f"unbatched_units={unbatched_units}"
        )
    removed_manifest_units = {
        manifest.batch_id: [
            unit_id for unit_id in manifest.unit_ids if unit_id not in unit_map
        ]
        for manifest in manifests
        if any(unit_id not in unit_map for unit_id in manifest.unit_ids)
    }
    if removed_manifest_units:
        raise ValueError(
            "Workflow manifests reference removed source units; recreate the "
            "affected batches before continuing: "
            f"removed_units={removed_manifest_units}"
        )
    stale_translatability = [
        manifest.batch_id
        for manifest in manifests
        if manifest.translatable_unit_ids
        != [
            unit_id
            for unit_id in manifest.unit_ids
            if unit_id in unit_map and unit_map[unit_id].translatable
        ]
    ]
    if stale_translatability:
        raise ValueError(
            "Workflow manifests have stale translatable-unit scope; refresh the "
            f"affected batches before continuing: batch_ids={stale_translatability}"
        )
    manifests = manifests[lower : upper + 1]
    context_cache: dict[
        tuple[str, ...], tuple[str, dict[str, str]] | None
    ] = {}
    stages = [
        (
            manifest.batch_id,
            _batch_stage(root, manifest.batch_id, snapshot, context_cache),
        )
        for manifest in manifests
    ]
    start = next((index for index, (_, stage) in enumerate(stages) if stage != "complete"), None)
    if start is None:
        return {
            "stage": "complete",
            "batch_ids": [],
            "host": resolved_host,
            "limit": resolved_limit,
            "start_at": start_at,
            "through": through,
        }
    stage = stages[start][1]
    batch_ids: list[str] = []
    selected_unit_ids: set[str] = set()
    for manifest, (batch_id, candidate_stage) in zip(
        manifests[start:], stages[start:], strict=True
    ):
        if candidate_stage != stage or len(batch_ids) >= resolved_limit:
            break
        candidate_ids = set(manifest.unit_ids)
        if len(candidate_ids) != len(manifest.unit_ids):
            raise ValueError(
                f"Workflow manifest {batch_id} contains duplicate source units"
            )
        if selected_unit_ids & candidate_ids:
            break
        batch_ids.append(batch_id)
        selected_unit_ids.update(candidate_ids)
    return {
        "stage": stage,
        "batch_ids": batch_ids,
        "host": resolved_host,
        "limit": resolved_limit,
        "start_at": start_at,
        "through": through,
    }


def workflow_status(root: Path, batch_ids: Iterable[str]) -> dict[str, Any]:
    """Return a compact status for an already-selected wave."""
    require_current_project_schema(root, "Workflow coordination")
    requested = list(batch_ids)
    if (
        not requested
        or len(requested) > WAVE_BATCH_SET_MAX
        or len(set(requested)) != len(requested)
    ):
        raise ValueError(
            f"workflow status requires 1 to {WAVE_BATCH_SET_MAX} unique batch IDs"
        )
    snapshot = _load_workflow_snapshot(root, set(requested))
    known = {manifest.batch_id for manifest in snapshot.manifests}
    missing = sorted(set(requested) - known)
    if missing:
        raise ValueError(f"Unknown batch IDs: {missing}")
    requested_manifests = [
        manifest for manifest in snapshot.manifests if manifest.batch_id in set(requested)
    ]
    removed_units = {
        manifest.batch_id: [
            unit_id for unit_id in manifest.unit_ids if unit_id not in snapshot.unit_map
        ]
        for manifest in requested_manifests
        if any(unit_id not in snapshot.unit_map for unit_id in manifest.unit_ids)
    }
    if removed_units:
        raise ValueError(
            "Workflow manifests reference removed source units; recreate the "
            f"affected batches before continuing: removed_units={removed_units}"
        )
    stale_translatability = [
        manifest.batch_id
        for manifest in requested_manifests
        if manifest.translatable_unit_ids
        != [
            unit_id
            for unit_id in manifest.unit_ids
            if snapshot.unit_map[unit_id].translatable
        ]
    ]
    if stale_translatability:
        raise ValueError(
            "Workflow manifests have stale translatable-unit scope; refresh the "
            "affected batches before continuing: "
            f"batch_ids={stale_translatability}"
        )
    covered_unit_ids = {
        unit_id for manifest in snapshot.manifests for unit_id in manifest.unit_ids
    }
    unbatched_units = sorted(
        unit.unit_id
        for unit in snapshot.units
        if unit.render_policy is RenderPolicy.INCLUDE
        and unit.unit_id not in covered_unit_ids
    )
    if unbatched_units:
        raise ValueError(
            "Workflow manifests do not cover current renderable source units; "
            "refresh or create batches before continuing: "
            f"unbatched_units={unbatched_units}"
        )
    context_cache: dict[
        tuple[str, ...], tuple[str, dict[str, str]] | None
    ] = {}
    stages = {
        batch_id: _batch_stage(root, batch_id, snapshot, context_cache)
        for batch_id in requested
    }
    unique_stages = set(stages.values())
    return {
        "batch_ids": requested,
        "stage": next(iter(unique_stages)) if len(unique_stages) == 1 else "mixed",
        "stages": stages,
        "complete": all(stage == "complete" for stage in stages.values()),
    }


def _validate_batch_set(root: Path, batch_ids: list[str]) -> list[Any]:
    if not 1 <= len(batch_ids) <= WAVE_BATCH_SET_MAX:
        raise ValueError(
            f"workflow packets require 1 to {WAVE_BATCH_SET_MAX} batch IDs"
        )
    if len(set(batch_ids)) != len(batch_ids):
        raise ValueError("workflow packet batch IDs must be unique")
    ordered = _all_manifests(root)
    requested_series = {_batch_series(batch_id) for batch_id in batch_ids}
    if len(requested_series) == 1 and None not in requested_series:
        active_series = next(iter(requested_series))
        ordered = [
            manifest
            for manifest in ordered
            if _batch_series(manifest.batch_id) == active_series
        ]
    index = {manifest.batch_id: position for position, manifest in enumerate(ordered)}
    missing = [batch_id for batch_id in batch_ids if batch_id not in index]
    if missing:
        raise ValueError(f"Unknown batch IDs: {missing}")
    positions = [index[batch_id] for batch_id in batch_ids]
    if positions != list(range(min(positions), min(positions) + len(positions))):
        raise ValueError("workflow packet batch IDs must be consecutive and ordered")
    selected = [ordered[position] for position in positions]
    current_units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    current_unit_map = {unit.unit_id: unit for unit in current_units}
    current_unit_ids = set(current_unit_map)
    removed_units = {
        manifest.batch_id: [
            unit_id
            for unit_id in manifest.unit_ids
            if unit_id not in current_unit_ids
        ]
        for manifest in selected
        if any(unit_id not in current_unit_ids for unit_id in manifest.unit_ids)
    }
    if removed_units:
        raise ValueError(
            "Workflow manifests reference removed source units; recreate the "
            f"affected batches before continuing: removed_units={removed_units}"
        )
    stale_translatability = [
        manifest.batch_id
        for manifest in selected
        if manifest.translatable_unit_ids
        != [
            unit_id
            for unit_id in manifest.unit_ids
            if current_unit_map[unit_id].translatable
        ]
    ]
    if stale_translatability:
        raise ValueError(
            "Workflow manifests have stale translatable-unit scope; refresh the "
            "affected batches before creating a packet: "
            f"batch_ids={stale_translatability}"
        )
    unit_counts = Counter(
        unit_id for manifest in selected for unit_id in manifest.unit_ids
    )
    overlapping = sorted(
        unit_id for unit_id, count in unit_counts.items() if count > 1
    )
    if overlapping:
        raise ValueError(
            "Workflow packet batches contain overlapping source units: "
            f"{overlapping}"
        )
    return selected


def _shared_context(root: Path, units: list[SourceUnit]) -> str:
    return audit_context_text(root, units)


def _audit_unit_text(unit: SourceUnit, record: TranslationRecord | None) -> str:
    source = (
        equation_markdown(unit)
        if unit.kind is UnitKind.EQUATION
        else unit.source_markdown or unit.source_text
    )
    if unit.table:
        source = "\n".join(" | ".join(row) for row in unit.table.rows)
    if unit.figure_labels:
        source += "\n\nFigure label sources:\n" + "\n".join(
            f"- {label.source}" for label in unit.figure_labels
        )
    target = record.target_text if record else "[source-only]"
    if record and unit.kind is UnitKind.CAPTION:
        target = normalize_zh_caption(target)
    if record and record.target_table:
        target = "\n".join(" | ".join(row) for row in record.target_table.rows)
    rendered_figure_labels = effective_figure_labels(unit, record)
    if rendered_figure_labels:
        target += "\n\nFigure label translations:\n" + "\n".join(
            f"- {label.source}: {label.target or '[missing]'}"
            for label in rendered_figure_labels
        )
    if record and record.reader_note:
        note = record.reader_note
        target += "\n\nReader note (separate from translated body):\n" + note.text
        if note.sources:
            target += "\nSources:\n" + "\n".join(f"- {source}" for source in note.sources)
        if note.accessed_at:
            target += f"\nAccessed: {note.accessed_at}"
    return (
        f"## {unit.unit_id} (p{unit.page};{unit.kind})\n\n"
        f"Source:\n\n{source}\n\nTranslation:\n\n{target}\n"
    )


def _audit_read_only_context(
    units: list[SourceUnit], translations: dict[str, TranslationRecord]
) -> str:
    return (
        "# Read-only seam context\n\noutside the requested batch set and review coverage.\n\n"
        + "\n".join(
            _audit_unit_text(unit, translations.get(unit.unit_id)) for unit in units
        )
    )


def _audit_packet_text(
    batch_id: str,
    lens: str,
    units: list[SourceUnit],
    translations: dict[str, TranslationRecord],
    has_read_only_context: bool,
) -> str:
    focus = {
        "fidelity": "Check fidelity, omissions, additions, references, and numbers.",
        "technical": "Check terminology, code, tables, formulas, and figures.",
        "chinese-style": "Check precise, idiomatic Simplified Chinese.",
    }[lens]
    return (
        f"# {lens} audit: {batch_id}\n\n{focus}\n\n"
        "Return ReviewIssue JSONL; empty means no issues.\n\n"
        + (
            "Consult read-only-context.md for semantic seam context. Its units "
            "are outside this packet's review coverage.\n\n"
            if has_read_only_context
            else ""
        )
        + "\n".join(
            _audit_unit_text(unit, translations.get(unit.unit_id)) for unit in units
        )
    )


def create_workflow_packet(
    root: Path,
    stage: str,
    batch_ids: list[str],
    lens: str | None = None,
) -> WorkflowPacketManifest | list[WorkflowPacketManifest]:
    require_current_project_schema(root, "Workflow packet creation")
    if stage not in {"translate", "audit"}:
        raise ValueError("workflow packet stage must be translate or audit")
    if stage == "audit" and len(batch_ids) > LENS_REVIEWER_BATCH_MAX:
        raise ValueError(
            "audit packets require at most "
            f"{LENS_REVIEWER_BATCH_MAX} consecutive batch IDs; split larger waves "
            "into consecutive groups"
        )
    if stage == "audit" and lens == "all":
        packets: list[WorkflowPacketManifest] = []
        for selected_lens in sorted(REQUIRED_AUDIT_LENSES):
            if not any(
                audit_coverage(root, batch_id)["missing"][selected_lens]
                for batch_id in batch_ids
            ):
                continue
            packet = create_workflow_packet(root, stage, batch_ids, selected_lens)
            if isinstance(packet, list):  # pragma: no cover - guarded above
                packets.extend(packet)
            else:
                packets.append(packet)
        return packets
    if stage == "audit" and lens not in REQUIRED_AUDIT_LENSES:
        raise ValueError(
            "audit packets require --lens all|fidelity|technical|chinese-style"
        )
    if stage == "translate" and lens is not None:
        raise ValueError("translation packets do not accept a lens")
    manifests = _validate_batch_set(root, batch_ids)
    all_units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    unit_map = {unit.unit_id: unit for unit in all_units}
    positions = {unit.unit_id: index for index, unit in enumerate(all_units)}
    translations = translation_map(root)
    batch_unit_ids = {
        manifest.batch_id: list(manifest.unit_ids) for manifest in manifests
    }
    batch_context_unit_ids = {
        manifest.batch_id: list(manifest.unit_ids) for manifest in manifests
    }
    if stage == "audit":
        batch_unit_ids = {}
        batch_context_unit_ids = {}
        for manifest in manifests:
            pending = audit_coverage(root, manifest.batch_id)["missing"][lens or ""]
            if not pending:
                continue
            batch_unit_ids[manifest.batch_id] = [
                unit_id for unit_id in manifest.unit_ids if unit_id in set(pending)
            ]
            batch_context_unit_ids[manifest.batch_id] = dependency_closure(
                root, [manifest.batch_id], pending
            )
        manifests = [
            manifest for manifest in manifests if manifest.batch_id in batch_unit_ids
        ]
        if not manifests:
            raise ValueError(f"No missing {lens} audit coverage for requested batches")
        batch_ids = [manifest.batch_id for manifest in manifests]
    packet_unit_ids = [
        unit_id
        for manifest in manifests
        for unit_id in batch_unit_ids[manifest.batch_id]
    ]
    selected_ids = list(
        dict.fromkeys(
            unit_id
            for manifest in manifests
            for unit_id in batch_context_unit_ids[manifest.batch_id]
        )
    )
    selected_units = [unit_map[unit_id] for unit_id in selected_ids if unit_id in unit_map]
    fingerprints = {
        unit.unit_id: translation_unit_fingerprint(
            unit, translations.get(unit.unit_id)
        )
        for unit in selected_units
    }
    batch_context_fingerprints = {
        manifest.batch_id: audit_evidence_context_fingerprint(
            sha256_text(
                _shared_context(
                    root,
                    [
                        unit_map[unit_id]
                        for unit_id in batch_context_unit_ids[manifest.batch_id]
                    ],
                )
            ),
            fingerprints,
            batch_context_unit_ids[manifest.batch_id],
        )
        for manifest in manifests
        if stage == "audit"
    }
    planned_files: dict[str, tuple[str, str]] = {
        "shared": ("shared.md", _shared_context(root, selected_units))
    }
    for manifest in manifests:
        batch_units = [
            unit_map[unit_id]
            for unit_id in batch_unit_ids[manifest.batch_id]
            if unit_id in unit_map
        ]
        if stage == "translate":
            planned_files[f"{manifest.batch_id}:source"] = (
                f"{manifest.batch_id}.source.md",
                batch_source_markdown(root, batch_units),
            )
            memory = translation_memory(root, manifest.unit_ids, limit=6)
            first = positions[manifest.unit_ids[0]]
            last = positions[manifest.unit_ids[-1]]
            adjacent = []
            if first:
                adjacent.append(all_units[first - 1])
            if last + 1 < len(all_units):
                adjacent.append(all_units[last + 1])
            context = ["# Retrieved approved translation memory", ""]
            if memory:
                for item in memory:
                    context.extend(
                        [
                            f"## {item['unit_id']}",
                            "",
                            f"Source: {item['source']}",
                            "",
                            f"Target: {item['target']}",
                            "",
                        ]
                    )
            else:
                context.extend(["None yet.", ""])
            context.extend(["# Adjacent source context", ""])
            context.extend(
                f"- {unit.unit_id}: {unit.source_text}" for unit in adjacent
            )
            planned_files[f"{manifest.batch_id}:context"] = (
                f"{manifest.batch_id}.context.md",
                "\n".join(context).rstrip() + "\n",
            )
        else:
            context_ids = batch_context_unit_ids[manifest.batch_id]
            read_only_units = [
                unit_map[unit_id]
                for unit_id in context_ids
                if unit_id not in set(batch_unit_ids[manifest.batch_id])
            ]
            has_read_only_context = bool(read_only_units)
            if read_only_units:
                context_key = (
                    "audit:read-only-context"
                    if len(manifests) == 1
                    else f"{manifest.batch_id}:read-only-context"
                )
                planned_files[context_key] = (
                    f"{manifest.batch_id}.read-only-context.md",
                    _audit_read_only_context(read_only_units, translations),
                )
            planned_files[f"{manifest.batch_id}:audit"] = (
                f"{manifest.batch_id}.audit.md",
                _audit_packet_text(
                    manifest.batch_id,
                    lens or "fidelity",
                    batch_units,
                    translations,
                    has_read_only_context,
                ),
            )

    planned_file_sha256 = {
        file_id: sha256_text(content)
        for file_id, (_, content) in planned_files.items()
    }
    identity = sha256_text(
        json.dumps(
            {
                "version": 3,
                "stage": stage,
                "lens": lens,
                "batch_unit_ids": batch_unit_ids,
                "batch_context_unit_ids": batch_context_unit_ids,
                "batch_context_fingerprints": batch_context_fingerprints,
                "unit_fingerprints": fingerprints,
                "file_sha256": planned_file_sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    packet_id = f"{stage}-{identity[:16]}"
    storage_root = ".littrans/work"
    packet_dir = root / storage_root / packet_id
    files = {
        file_id: str((packet_dir / filename).relative_to(root)).replace("\\", "/")
        for file_id, (filename, _) in planned_files.items()
    }
    manifest = WorkflowPacketManifest(
        packet_id=packet_id,
        stage=stage,
        batch_ids=batch_ids,
        lens=lens,
        unit_ids=packet_unit_ids,
        unit_fingerprints=fingerprints,
        batch_unit_ids=batch_unit_ids,
        batch_context_unit_ids=batch_context_unit_ids,
        batch_context_fingerprints=batch_context_fingerprints,
        storage_root=storage_root,
        files=files,
        file_sha256=planned_file_sha256,
        total_bytes=sum(
            len(content.encode("utf-8")) for _, content in planned_files.values()
        ),
    )
    existing_path = packet_dir / "manifest.json"
    if existing_path.is_file():
        try:
            existing = WorkflowPacketManifest.model_validate(read_json(existing_path))
        except (OSError, ValueError):
            existing = None
        if existing is not None:
            manifest = manifest.model_copy(update={"created_at": existing.created_at})
            if existing == manifest and all(
                (path := root / files[file_id]).is_file()
                and path.stat().st_size == len(content.encode("utf-8"))
                and sha256_file(path) == planned_file_sha256[file_id]
                for file_id, (_, content) in planned_files.items()
            ):
                return existing
    for filename, content in planned_files.values():
        path = packet_dir / filename
        atomic_write_text(path, content)
    manifest_path = packet_dir / "manifest.json"
    write_json(manifest_path, manifest.model_dump(mode="json"))
    return manifest


def import_review_set(
    root: Path, packet_manifest_path: Path, issues_path: Path
) -> dict[str, Any]:
    require_current_project_schema(root, "Review-set import")
    supplied_manifest = WorkflowPacketManifest.model_validate(
        read_json(packet_manifest_path)
    )
    if supplied_manifest.storage_root not in {"packets", ".littrans/work"}:
        raise ValueError("Unsupported audit packet storage root")
    packet_root = (root / supplied_manifest.storage_root).resolve()
    packet_dir = (packet_root / supplied_manifest.packet_id).resolve()
    try:
        packet_dir.relative_to(packet_root)
    except ValueError as exc:
        raise ValueError("Audit packet path escapes the project packet root") from exc
    compatibility_manifest = (
        root / "packets" / supplied_manifest.packet_id / "manifest.json"
    ).resolve()
    supplied_path = packet_manifest_path.resolve()
    canonical_path = (
        compatibility_manifest
        if supplied_path == compatibility_manifest
        else (packet_dir / "manifest.json").resolve()
    )
    if canonical_path != compatibility_manifest:
        try:
            canonical_path.relative_to(packet_root)
        except ValueError as exc:
            raise ValueError("Canonical audit manifest escapes the packet root") from exc
    if not canonical_path.is_file():
        raise ValueError(
            f"Canonical audit packet manifest does not exist: {canonical_path}"
        )
    manifest = WorkflowPacketManifest.model_validate(read_json(canonical_path))
    if supplied_manifest != manifest:
        raise ValueError(
            "Provided audit packet manifest does not match the canonical stored manifest"
        )
    if manifest.stage != "audit" or manifest.lens not in REQUIRED_AUDIT_LENSES:
        raise ValueError("review import-set requires an audit packet manifest")
    if len(manifest.batch_ids) != len(set(manifest.batch_ids)):
        raise ValueError("Audit packet batch IDs must be unique")
    required_files = {
        *(f"{batch_id}:audit" for batch_id in manifest.batch_ids),
        *(
            (f"{batch_id}:shared" for batch_id in manifest.batch_ids)
            if manifest.batch_context_unit_ids
            and all(
                f"{batch_id}:shared" in manifest.files
                for batch_id in manifest.batch_ids
            )
            else ("shared",)
        ),
    }
    missing_files = sorted(required_files - set(manifest.files))
    if missing_files:
        raise ValueError(
            f"Audit packet manifest is missing required review files: {missing_files}"
        )
    if set(manifest.file_sha256) != set(manifest.files):
        raise ValueError(
            "Audit packet manifest must contain one digest for every packet file"
        )
    packet_bytes = 0
    for file_id, relative_path in manifest.files.items():
        packet_path = (root / relative_path).resolve()
        try:
            packet_path.relative_to(packet_dir)
        except ValueError as exc:
            raise ValueError(
                f"Audit packet file escapes its packet directory: {file_id}"
            ) from exc
        if not packet_path.is_file():
            raise ValueError(f"Audit packet file is missing: {file_id}")
        if sha256_file(packet_path) != manifest.file_sha256[file_id]:
            raise ValueError(f"Audit packet file digest mismatch: {file_id}")
        packet_bytes += packet_path.stat().st_size
    if packet_bytes != manifest.total_bytes:
        raise ValueError("Audit packet total_bytes does not match its stored files")
    missing_fingerprints = sorted(
        set(manifest.unit_ids) - set(manifest.unit_fingerprints)
    )
    if missing_fingerprints:
        raise ValueError(
            "Audit packet is missing fingerprints for covered units: "
            f"{missing_fingerprints}"
        )
    units = {
        unit.unit_id: unit
        for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    }
    translations = translation_map(root)
    current = {
        unit_id: translation_unit_fingerprint(units[unit_id], translations.get(unit_id))
        for unit_id in manifest.unit_fingerprints
        if unit_id in units
    }
    stale = [
        unit_id
        for unit_id, fingerprint in manifest.unit_fingerprints.items()
        if current.get(unit_id) != fingerprint
    ]
    if stale:
        raise ValueError(f"Audit packet is stale for units: {stale}")
    source_issues = read_jsonl(issues_path, ReviewIssue)
    canonicalize_ids = bool(manifest.batch_unit_ids)
    issues: list[ReviewIssue] = []
    id_map: dict[str, str] = {}
    occurrence: Counter[tuple[str, str]] = Counter()
    for issue in source_issues:
        key = (issue.batch_id, issue.issue_id)
        occurrence[key] += 1
        ordinal = occurrence[key]
        canonical_id = (
            "audit-"
            + sha256_text(
                f"{manifest.packet_id}|{manifest.lens}|{issue.batch_id}|"
                f"{issue.issue_id}|{ordinal}"
            )[:24]
            if canonicalize_ids
            else issue.issue_id
        )
        map_key = f"{issue.batch_id}:{issue.issue_id}"
        if ordinal > 1:
            map_key += f"#{ordinal}"
        id_map[map_key] = canonical_id
        issues.append(issue.model_copy(update={"issue_id": canonical_id}))
    batches = {
        batch_id: load_manifest(root, batch_id) for batch_id in manifest.batch_ids
    }
    covered_unit_ids = set(manifest.unit_ids)
    by_batch: dict[str, list[ReviewIssue]] = {
        batch_id: [] for batch_id in manifest.batch_ids
    }
    for issue in issues:
        if issue.batch_id not in by_batch:
            raise ValueError(f"Issue {issue.issue_id} is outside the packet batch set")
        if issue.unit_id not in covered_unit_ids:
            raise ValueError(
                f"Issue {issue.issue_id} targets a unit outside the audit packet coverage: "
                f"{issue.unit_id}"
            )
        if issue.unit_id not in batches[issue.batch_id].unit_ids:
            raise ValueError(
                f"Issue {issue.issue_id} targets a unit outside batch "
                f"{issue.batch_id}: {issue.unit_id}"
            )
        by_batch[issue.batch_id].append(issue)

    imported = {
        batch_id: len(by_batch[batch_id]) for batch_id in manifest.batch_ids
    }
    with project_write_lock(root):
        plans = []
        for batch_id in manifest.batch_ids:
            batch = batches[batch_id]
            coverage_ids = manifest.batch_unit_ids.get(
                batch_id,
                [unit_id for unit_id in batch.unit_ids if unit_id in manifest.unit_ids],
            )
            if not coverage_ids:
                continue
            context_ids = manifest.batch_context_unit_ids.get(
                batch_id, list(manifest.unit_fingerprints)
            )
            plans.append(
                _prepare_review_import_locked(
                    root=root,
                    batch_id=batch_id,
                    issues=by_batch[batch_id],
                    lenses=[manifest.lens],
                    covered_unit_ids=coverage_ids,
                    reviewer=(
                        by_batch[batch_id][0].reviewer
                        if by_batch[batch_id]
                        else f"independent-{manifest.lens}-auditor"
                    ),
                    packet_id=manifest.packet_id,
                    expected_unit_fingerprints={
                        unit_id: manifest.unit_fingerprints[unit_id]
                        for unit_id in coverage_ids
                    },
                    expected_context_fingerprint=(
                        manifest.batch_context_fingerprints.get(batch_id)
                        or audit_evidence_context_fingerprint(
                            manifest.file_sha256["shared"],
                            manifest.unit_fingerprints,
                            context_ids,
                        )
                    ),
                    context_unit_ids=context_ids,
                )
            )
        mutation_paths = [root / "translations" / "current.jsonl", root / "project.yaml"]
        for plan in plans:
            mutation_paths.extend(
                [
                    root / "reviews" / f"{plan.batch_id}.issues.jsonl",
                    root / "evidence" / "audits" / f"{plan.batch_id}.jsonl",
                    root / "reviews" / f"{plan.batch_id}.audit.json",
                ]
            )
        snapshots = snapshot_files(mutation_paths)
        try:
            for plan in plans:
                _apply_review_import_locked(root, plan)
        except BaseException:
            restore_files(snapshots)
            raise
    return {
        "packet_id": manifest.packet_id,
        "lens": manifest.lens,
        "imported": imported,
        "id_map": id_map,
    }


def _allocated_packet_bytes(
    packet: WorkflowPacketManifest, selected_batch_ids: set[str]
) -> int:
    """Allocate every packet byte equally and deterministically across its batches."""
    if not packet.batch_ids:
        return 0
    quotient, remainder = divmod(packet.total_bytes, len(packet.batch_ids))
    return sum(
        quotient + (index < remainder)
        for index, batch_id in enumerate(packet.batch_ids)
        if batch_id in selected_batch_ids
    )


def prune_workflow_packets(
    root: Path,
    batch_ids: Iterable[str] | None = None,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """List or remove packets already represented by authoritative audit evidence."""
    require_current_project_schema(root, "Workflow packet pruning")
    selected = set(batch_ids or ())
    known = {manifest.batch_id for manifest in _all_manifests(root)}
    missing = sorted(selected - known)
    if missing:
        raise ValueError(f"Unknown batch IDs: {missing}")
    audit_runs = {
        batch_id: read_jsonl(
            root / "evidence" / "audits" / f"{batch_id}.jsonl", AuditRun
        )
        for batch_id in known
    }
    candidates: dict[str, tuple[WorkflowPacketManifest, list[Path], int]] = {}
    for packet_root in (root / ".littrans" / "work", root / "packets"):
        if not packet_root.is_dir():
            continue
        for path in packet_root.glob("*/manifest.json"):
            manifest = WorkflowPacketManifest.model_validate(read_json(path))
            required_batches = [
                batch_id
                for batch_id in manifest.batch_ids
                if manifest.batch_unit_ids.get(batch_id, manifest.unit_ids)
            ]
            completely_imported = (
                manifest.stage == "audit"
                and bool(required_batches)
                and all(
                any(
                    run.packet_id == manifest.packet_id
                    and run.lens == manifest.lens
                    and set(run.unit_fingerprints)
                    >= set(manifest.batch_unit_ids.get(batch_id, manifest.unit_ids))
                    for run in audit_runs.get(batch_id, [])
                )
                for batch_id in required_batches
                )
            )
            if not completely_imported:
                continue
            if selected and not selected.intersection(manifest.batch_ids):
                continue
            packet_dir = path.parent.resolve()
            packet_dir.relative_to(packet_root.resolve())
            size = sum(item.stat().st_size for item in packet_dir.rglob("*") if item.is_file())
            existing = candidates.get(manifest.packet_id)
            if existing:
                candidates[manifest.packet_id] = (
                    manifest,
                    [*existing[1], packet_dir],
                    existing[2] + size,
                )
            else:
                candidates[manifest.packet_id] = (manifest, [packet_dir], size)
    removed: list[str] = []
    if apply:
        with project_write_lock(root):
            for packet_id, (_, packet_dirs, _) in candidates.items():
                for packet_dir in packet_dirs:
                    shutil.rmtree(packet_dir)
                removed.append(packet_id)
    return {
        "mode": "apply" if apply else "dry-run",
        "candidates": list(candidates),
        "candidate_bytes": sum(size for _, _, size in candidates.values()),
        "removed": removed,
    }


def workflow_metrics(root: Path, batch_ids: Iterable[str] | None = None) -> dict[str, Any]:
    all_manifests = _all_manifests(root)
    known = {manifest.batch_id for manifest in all_manifests}
    selected = known if batch_ids is None else set(batch_ids)
    missing = sorted(selected - known)
    if missing:
        raise ValueError(f"Unknown batch IDs: {missing}")
    manifests = [manifest for manifest in all_manifests if manifest.batch_id in selected]
    selected_units = {
        unit_id for manifest in manifests for unit_id in manifest.translatable_unit_ids
    }
    selected_pages = {page for manifest in manifests for page in manifest.pages}
    history = [
        record
        for record in read_jsonl(root / "translations" / "history.jsonl", TranslationRecord)
        if record.unit_id in selected_units
    ]
    units = {
        unit.unit_id: unit
        for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    }
    history = [record for record in history if record.unit_id in units]
    previous: dict[str, TranslationRecord] = {}
    semantic_noops = 0
    for record in history:
        prior = previous.get(record.unit_id)
        if prior is not None and translations_semantically_equal(
            units[record.unit_id], prior, record
        ):
            semantic_noops += 1
        previous[record.unit_id] = record
    legacy_packet_bytes = sum(
        sum(
            path.stat().st_size
            for path in (
                root / "batches" / manifest.batch_id / "source.md",
                root / "batches" / manifest.batch_id / "context.md",
            )
            if path.is_file()
        )
        for manifest in manifests
    )
    packet_by_id: dict[str, WorkflowPacketManifest] = {}
    for packet_root in (root / "packets", root / ".littrans" / "work"):
        for path in packet_root.glob("*/manifest.json"):
            packet = WorkflowPacketManifest.model_validate(read_json(path))
            packet_by_id[packet.packet_id] = packet
    packet_manifests = list(packet_by_id.values())
    external_runs = []
    for batch_id in selected:
        external_runs.extend(
            read_jsonl(
                root / "reviews" / f"{batch_id}.external-runs.jsonl",
                ExternalReviewRun,
            )
        )
    token_totals: Counter[str] = Counter()
    duration = 0.0
    cost = 0.0
    for run in external_runs:
        duration += run.duration_seconds or 0.0
        cost += run.cost_usd or 0.0
        if run.usage:
            for field, value in run.usage.model_dump().items():
                token_totals[field] += value
    audit_runs = [
        run
        for batch_id in selected
        for run in read_jsonl(
            root / "evidence" / "audits" / f"{batch_id}.jsonl", AuditRun
        )
    ]
    logical_audit_calls = {
        (run.packet_id or run.run_id, run.lens) for run in audit_runs
    }
    return {
        "batch_ids": [manifest.batch_id for manifest in manifests],
        "history_records": len(history),
        "semantic_noop_records": semantic_noops,
        "semantic_noop_ratio": semantic_noops / len(history) if history else 0.0,
        "legacy_packet_bytes": legacy_packet_bytes,
        "generated_packet_bytes": sum(
            _allocated_packet_bytes(packet, selected) for packet in packet_manifests
        ),
        "generated_packet_allocation": "equal-per-batch-leading-remainder",
        "page_receipts": sum(
            (root / "evidence" / "pages" / f"page-{page:04}.json").is_file()
            for page in selected_pages
        ),
        "audit_runs": len(audit_runs),
        "audit_evidence_rows": len(audit_runs),
        "logical_audit_calls": len(logical_audit_calls),
        "external_runs": len(external_runs),
        "external_attempts": sum(run.attempts for run in external_runs),
        "external_provider_turns": token_totals["provider_turns"],
        "external_cached_input_tokens": token_totals["cache_read_input_tokens"],
        "external_non_cached_input_tokens": max(
            token_totals["input_tokens"] - token_totals["cache_read_input_tokens"],
            0,
        ),
        "external_duration_seconds": duration,
        "external_cost_usd": cost,
        "external_usage": dict(token_totals),
    }
