from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable
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
from littrans.models import (
    AuditRun,
    ExternalReviewRun,
    IssueStatus,
    ProjectStatus,
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
    qa_report_is_current,
)
from littrans.storage import (
    atomic_write_text,
    load_project,
    project_write_lock,
    read_json,
    read_jsonl,
    require_current_project_schema,
    sha256_file,
    write_json,
)


def _all_manifests(root: Path) -> list[Any]:
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


def _batch_stage(root: Path, batch_id: str) -> str:
    manifest = load_manifest(root, batch_id)
    translations = translation_map(root)
    if any(unit_id not in translations for unit_id in manifest.translatable_unit_ids):
        return "translate"
    if not qa_report_is_current(root, batch_id):
        return "qa"
    if not audit_coverage(root, batch_id)["complete"]:
        return "audit"
    config = load_project(root)
    open_issues = [
        issue
        for issue in read_jsonl(
            root / "reviews" / f"{batch_id}.issues.jsonl", ReviewIssue
        )
        if issue.status is IssueStatus.OPEN
    ]
    external_enabled = bool(
        config.external_review and config.external_review.enabled
    )
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
        from littrans.external_review import external_review_status

        if not external_review_status(root, batch_id)["external_approvable"]:
            return "external-review"
        if any(
            translations[unit_id].status
            not in {ProjectStatus.EXTERNAL_REVIEWED, ProjectStatus.HUMAN_APPROVED}
            for unit_id in manifest.translatable_unit_ids
        ):
            return "external-approve"
    return "complete"


def workflow_next(root: Path, limit: int = 3) -> dict[str, Any]:
    require_current_project_schema(root, "Workflow coordination")
    if not 1 <= limit <= 3:
        raise ValueError("workflow next limit must be between 1 and 3")
    manifests = _all_manifests(root)
    units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    unit_map = {unit.unit_id: unit for unit in units}
    manifest_unit_ids = {
        unit_id for manifest in manifests for unit_id in manifest.unit_ids
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
    stages = [(manifest.batch_id, _batch_stage(root, manifest.batch_id)) for manifest in manifests]
    start = next((index for index, (_, stage) in enumerate(stages) if stage != "complete"), None)
    if start is None:
        return {"stage": "complete", "batch_ids": [], "limit": limit}
    stage = stages[start][1]
    batch_ids: list[str] = []
    selected_unit_ids: set[str] = set()
    for manifest, (batch_id, candidate_stage) in zip(
        manifests[start:], stages[start:], strict=True
    ):
        if candidate_stage != stage or len(batch_ids) >= limit:
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
    return {"stage": stage, "batch_ids": batch_ids, "limit": limit}


def _validate_batch_set(root: Path, batch_ids: list[str]) -> list[Any]:
    if not 1 <= len(batch_ids) <= 3:
        raise ValueError("workflow packets require one to three batch IDs")
    if len(set(batch_ids)) != len(batch_ids):
        raise ValueError("workflow packet batch IDs must be unique")
    ordered = _all_manifests(root)
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
        source += "\n" + "\n".join(" | ".join(row) for row in unit.table.rows)
    if unit.figure_labels:
        source += "\n\nFigure label sources:\n" + "\n".join(
            f"- {label.source}" for label in unit.figure_labels
        )
    target = record.target_text if record else "[source-only]"
    if record and record.target_table:
        target += "\n" + "\n".join(" | ".join(row) for row in record.target_table.rows)
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
        f"## {unit.unit_id} (page {unit.page}; {unit.kind})\n\n"
        f"### Source\n\n{source}\n\n### Translation\n\n{target}\n"
    )


def _audit_read_only_context(
    units: list[SourceUnit], translations: dict[str, TranslationRecord]
) -> str:
    return (
        "# Read-only semantic seam context\n\n"
        "These units are outside the requested batch set. Use them to inspect "
        "continuations and cross-batch seams, but do not treat them as reviewed "
        "coverage for this packet.\n\n"
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
        "fidelity": "Check fidelity, omissions, additions, references, numbers, and evidence.",
        "technical": "Check terminology, code, tables, formulas, figures, and technical correctness.",
        "chinese-style": "Check precise, idiomatic Simplified Chinese without changing meaning.",
    }[lens]
    return (
        f"# Independent {lens} audit: {batch_id}\n\n{focus}\n\n"
        "Do not read prior issues. Return ReviewIssue JSONL only; an empty file means no issues.\n\n"
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
) -> WorkflowPacketManifest:
    require_current_project_schema(root, "Workflow packet creation")
    if stage not in {"translate", "audit"}:
        raise ValueError("workflow packet stage must be translate or audit")
    if stage == "audit" and lens not in REQUIRED_AUDIT_LENSES:
        raise ValueError("audit packets require --lens fidelity|technical|chinese-style")
    if stage == "translate" and lens is not None:
        raise ValueError("translation packets do not accept a lens")
    manifests = _validate_batch_set(root, batch_ids)
    all_units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    unit_map = {unit.unit_id: unit for unit in all_units}
    positions = {unit.unit_id: index for index, unit in enumerate(all_units)}
    translations = translation_map(root)
    requested_ids = [unit_id for manifest in manifests for unit_id in manifest.unit_ids]
    pending_ids = set(requested_ids)
    selected_ids = list(requested_ids)

    if stage == "audit":
        pending_ids = {
            unit_id
            for manifest in manifests
            for unit_id in audit_coverage(root, manifest.batch_id)["missing"][lens or ""]
        }
        selected_ids = (
            dependency_closure(root, batch_ids, pending_ids)
            if pending_ids
            else []
        )
    packet_unit_ids = [
        unit_id for unit_id in requested_ids if unit_id in pending_ids
    ]
    coverage_set = set(packet_unit_ids)
    selected_units = [unit_map[unit_id] for unit_id in selected_ids if unit_id in unit_map]
    packet_id = f"{stage}-{uuid.uuid4().hex[:12]}"
    packet_dir = root / "packets" / packet_id
    files: dict[str, str] = {}

    shared_path = packet_dir / "shared.md"
    atomic_write_text(shared_path, _shared_context(root, selected_units))
    files["shared"] = str(shared_path.relative_to(root)).replace("\\", "/")

    read_only_context_path: Path | None = None
    if stage == "audit":
        context_units = [
            unit_map[unit_id]
            for unit_id in selected_ids
            if unit_id in unit_map and unit_id not in coverage_set
        ]
        if context_units:
            read_only_context_path = packet_dir / "read-only-context.md"
            atomic_write_text(
                read_only_context_path,
                _audit_read_only_context(context_units, translations),
            )
            files["audit:read-only-context"] = str(
                read_only_context_path.relative_to(root)
            ).replace("\\", "/")

    for manifest in manifests:
        batch_units = [
            unit_map[unit_id]
            for unit_id in manifest.unit_ids
            if unit_id in unit_map
            and (stage == "translate" or unit_id in coverage_set)
        ]
        if stage == "translate":
            target_path = packet_dir / f"{manifest.batch_id}.source.md"
            atomic_write_text(
                target_path,
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
            context_path = packet_dir / f"{manifest.batch_id}.context.md"
            atomic_write_text(context_path, "\n".join(context).rstrip() + "\n")
            files[f"{manifest.batch_id}:source"] = str(
                target_path.relative_to(root)
            ).replace("\\", "/")
            files[f"{manifest.batch_id}:context"] = str(
                context_path.relative_to(root)
            ).replace("\\", "/")
        else:
            audit_path = packet_dir / f"{manifest.batch_id}.audit.md"
            atomic_write_text(
                audit_path,
                _audit_packet_text(
                    manifest.batch_id,
                    lens or "fidelity",
                    batch_units,
                    translations,
                    read_only_context_path is not None,
                ),
            )
            files[f"{manifest.batch_id}:audit"] = str(
                audit_path.relative_to(root)
            ).replace("\\", "/")

    fingerprints = {
        unit.unit_id: translation_unit_fingerprint(
            unit, translations.get(unit.unit_id)
        )
        for unit in selected_units
    }
    total_bytes = sum((root / path).stat().st_size for path in files.values())
    file_sha256 = {
        file_id: sha256_file(root / path) for file_id, path in files.items()
    }
    manifest = WorkflowPacketManifest(
        packet_id=packet_id,
        stage=stage,
        batch_ids=batch_ids,
        lens=lens,
        unit_ids=packet_unit_ids,
        unit_fingerprints=fingerprints,
        files=files,
        file_sha256=file_sha256,
        total_bytes=total_bytes,
    )
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
    packet_root = (root / "packets").resolve()
    packet_dir = (packet_root / supplied_manifest.packet_id).resolve()
    try:
        packet_dir.relative_to(packet_root)
    except ValueError as exc:
        raise ValueError("Audit packet path escapes the project packet root") from exc
    canonical_path = (packet_dir / "manifest.json").resolve()
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
        "shared",
        *(f"{batch_id}:audit" for batch_id in manifest.batch_ids),
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
    issues = read_jsonl(issues_path, ReviewIssue)
    batches = {
        batch_id: load_manifest(root, batch_id) for batch_id in manifest.batch_ids
    }
    covered_unit_ids = set(manifest.unit_ids)
    by_batch: dict[str, list[ReviewIssue]] = {batch_id: [] for batch_id in manifest.batch_ids}
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
            coverage_ids = [
                unit_id
                for unit_id in batch.unit_ids
                if unit_id in manifest.unit_ids
            ]
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
                    expected_context_fingerprint=audit_evidence_context_fingerprint(
                        manifest.file_sha256["shared"],
                        manifest.unit_fingerprints,
                        list(manifest.unit_fingerprints),
                    ),
                    context_unit_ids=list(manifest.unit_fingerprints),
                )
            )
        for plan in plans:
            _apply_review_import_locked(root, plan)
    return {"packet_id": manifest.packet_id, "lens": manifest.lens, "imported": imported}


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
    packet_manifests = [
        WorkflowPacketManifest.model_validate(read_json(path))
        for path in (root / "packets").glob("*/manifest.json")
    ]
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
        "audit_runs": sum(
            len(
                read_jsonl(
                    root / "evidence" / "audits" / f"{batch_id}.jsonl",
                    AuditRun,
                )
            )
            for batch_id in selected
        ),
        "external_runs": len(external_runs),
        "external_duration_seconds": duration,
        "external_cost_usd": cost,
        "external_usage": dict(token_totals),
    }
