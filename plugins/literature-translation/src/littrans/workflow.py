from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from littrans.batching import load_manifest
from littrans.evidence import (
    dependency_closure,
    relevant_terms,
    translation_memory,
    translation_payload,
    translation_unit_fingerprint,
)
from littrans.models import (
    AuditRun,
    ExternalReviewRun,
    ProjectStatus,
    ReviewIssue,
    SourceUnit,
    TranslationRecord,
    WorkflowPacketManifest,
)
from littrans.project import translation_map
from littrans.quality import (
    REQUIRED_AUDIT_LENSES,
    audit_coverage,
    import_review,
    qa_report_is_current,
)
from littrans.storage import (
    atomic_write_text,
    load_project,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
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
    config = load_project(root)
    if config.external_review and config.external_review.enabled:
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
    if not 1 <= limit <= 3:
        raise ValueError("workflow next limit must be between 1 and 3")
    manifests = _all_manifests(root)
    stages = [(manifest.batch_id, _batch_stage(root, manifest.batch_id)) for manifest in manifests]
    start = next((index for index, (_, stage) in enumerate(stages) if stage != "complete"), None)
    if start is None:
        return {"stage": "complete", "batch_ids": [], "limit": limit}
    stage = stages[start][1]
    batch_ids: list[str] = []
    for batch_id, candidate_stage in stages[start:]:
        if candidate_stage != stage or len(batch_ids) >= limit:
            break
        batch_ids.append(batch_id)
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
    return [ordered[position] for position in positions]


def _shared_context(root: Path, units: list[SourceUnit]) -> str:
    brief = (root / "context" / "document-brief.md").read_text(encoding="utf-8").strip()
    style = (root / "context" / "style-guide.md").read_text(encoding="utf-8").strip()
    terms = yaml.safe_dump(
        {"approved_terms": relevant_terms(root, units)},
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    return (
        f"# Document brief\n\n{brief}\n\n# Translation style\n\n{style}\n\n"
        f"# Relevant approved terminology\n\n```yaml\n{terms}\n```\n"
    )


def _audit_unit_text(unit: SourceUnit, record: TranslationRecord | None) -> str:
    source = unit.source_markdown or unit.source_text
    if unit.table:
        source += "\n" + "\n".join(" | ".join(row) for row in unit.table.rows)
    target = record.target_text if record else "[source-only]"
    if record and record.target_table:
        target += "\n" + "\n".join(" | ".join(row) for row in record.target_table.rows)
    return (
        f"## {unit.unit_id} (page {unit.page}; {unit.kind})\n\n"
        f"### Source\n\n{source}\n\n### Translation\n\n{target}\n"
    )


def create_workflow_packet(
    root: Path,
    stage: str,
    batch_ids: list[str],
    lens: str | None = None,
) -> WorkflowPacketManifest:
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
    selected_ids = list(requested_ids)

    if stage == "audit":
        pending = {
            unit_id
            for manifest in manifests
            for unit_id in audit_coverage(root, manifest.batch_id)["missing"][lens or ""]
        }
        selected_ids = dependency_closure(root, batch_ids, pending) if pending else []
    selected_units = [unit_map[unit_id] for unit_id in selected_ids if unit_id in unit_map]
    packet_id = f"{stage}-{uuid.uuid4().hex[:12]}"
    packet_dir = root / "packets" / packet_id
    files: dict[str, str] = {}

    shared_path = packet_dir / "shared.md"
    atomic_write_text(shared_path, _shared_context(root, selected_units))
    files["shared"] = str(shared_path.relative_to(root)).replace("\\", "/")

    read_only_context_path: Path | None = None
    if stage == "audit":
        requested_set = set(requested_ids)
        context_units = [
            unit_map[unit_id]
            for unit_id in selected_ids
            if unit_id in unit_map and unit_id not in requested_set
        ]
        if context_units:
            read_only_context_path = packet_dir / "read-only-context.md"
            context_body = (
                "# Read-only semantic seam context\n\n"
                "These units are outside the requested batch set. Use them to inspect "
                "continuations and cross-batch seams, but do not treat them as reviewed "
                "coverage for this packet.\n\n"
                + "\n".join(
                    _audit_unit_text(unit, translations.get(unit.unit_id))
                    for unit in context_units
                )
            )
            atomic_write_text(read_only_context_path, context_body)
            files["audit:read-only-context"] = str(
                read_only_context_path.relative_to(root)
            ).replace("\\", "/")

    for manifest in manifests:
        batch_units = [
            unit_map[unit_id]
            for unit_id in manifest.unit_ids
            if unit_id in unit_map and (stage == "translate" or unit_id in set(selected_ids))
        ]
        if stage == "translate":
            source_path = root / "batches" / manifest.batch_id / "source.md"
            target_path = packet_dir / f"{manifest.batch_id}.source.md"
            atomic_write_text(target_path, source_path.read_text(encoding="utf-8"))
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
            focus = {
                "fidelity": "Check fidelity, omissions, additions, references, numbers, and evidence.",
                "technical": "Check terminology, code, tables, formulas, figures, and technical correctness.",
                "chinese-style": "Check precise, idiomatic Simplified Chinese without changing meaning.",
            }[lens or "fidelity"]
            body = (
                f"# Independent {lens} audit: {manifest.batch_id}\n\n{focus}\n\n"
                "Do not read prior issues. Return ReviewIssue JSONL only; an empty file means no issues.\n\n"
                + (
                    "Consult read-only-context.md for semantic seam context. Its units "
                    "are outside this packet's review coverage.\n\n"
                    if read_only_context_path is not None
                    else ""
                )
                + "\n".join(_audit_unit_text(unit, translations.get(unit.unit_id)) for unit in batch_units)
            )
            atomic_write_text(audit_path, body)
            files[f"{manifest.batch_id}:audit"] = str(
                audit_path.relative_to(root)
            ).replace("\\", "/")

    requested_set = set(requested_ids)
    packet_unit_ids = [
        unit_id for unit_id in selected_ids if unit_id in requested_set
    ]
    fingerprints = {
        unit.unit_id: translation_unit_fingerprint(
            unit, translations.get(unit.unit_id)
        )
        for unit in selected_units
    }
    total_bytes = sum((root / path).stat().st_size for path in files.values())
    manifest = WorkflowPacketManifest(
        packet_id=packet_id,
        stage=stage,
        batch_ids=batch_ids,
        lens=lens,
        unit_ids=packet_unit_ids,
        unit_fingerprints=fingerprints,
        files=files,
        total_bytes=total_bytes,
    )
    manifest_path = packet_dir / "manifest.json"
    write_json(manifest_path, manifest.model_dump(mode="json"))
    return manifest


def import_review_set(
    root: Path, packet_manifest_path: Path, issues_path: Path
) -> dict[str, Any]:
    manifest = WorkflowPacketManifest.model_validate(read_json(packet_manifest_path))
    if manifest.stage != "audit" or manifest.lens not in REQUIRED_AUDIT_LENSES:
        raise ValueError("review import-set requires an audit packet manifest")
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
    by_batch: dict[str, list[ReviewIssue]] = {batch_id: [] for batch_id in manifest.batch_ids}
    for issue in issues:
        if issue.batch_id not in by_batch:
            raise ValueError(f"Issue {issue.issue_id} is outside the packet batch set")
        by_batch[issue.batch_id].append(issue)

    imported: dict[str, int] = {}
    for batch_id in manifest.batch_ids:
        batch = load_manifest(root, batch_id)
        coverage_ids = [
            unit_id
            for unit_id in batch.unit_ids
            if unit_id in manifest.unit_ids
        ]
        temporary = root / "packets" / manifest.packet_id / f".{batch_id}.import.jsonl"
        write_jsonl(temporary, by_batch[batch_id])
        try:
            import_review(
                root,
                batch_id,
                temporary,
                [manifest.lens],
                covered_unit_ids=coverage_ids,
                reviewer=(
                    by_batch[batch_id][0].reviewer
                    if by_batch[batch_id]
                    else f"independent-{manifest.lens}-auditor"
                ),
                packet_id=manifest.packet_id,
            )
        finally:
            temporary.unlink(missing_ok=True)
        imported[batch_id] = len(by_batch[batch_id])
    return {"packet_id": manifest.packet_id, "lens": manifest.lens, "imported": imported}


def workflow_metrics(root: Path, batch_ids: Iterable[str] | None = None) -> dict[str, Any]:
    selected = set(batch_ids or [manifest.batch_id for manifest in _all_manifests(root)])
    manifests = [manifest for manifest in _all_manifests(root) if manifest.batch_id in selected]
    selected_units = {
        unit_id for manifest in manifests for unit_id in manifest.translatable_unit_ids
    }
    history = [
        record
        for record in read_jsonl(root / "translations" / "history.jsonl", TranslationRecord)
        if record.unit_id in selected_units
    ]
    previous: dict[str, TranslationRecord] = {}
    semantic_noops = 0
    for record in history:
        prior = previous.get(record.unit_id)
        if prior is not None and translation_payload(prior) == translation_payload(record):
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
    packet_manifests = [
        packet for packet in packet_manifests if set(packet.batch_ids) <= selected
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
        "generated_packet_bytes": sum(packet.total_bytes for packet in packet_manifests),
        "page_receipts": len(list((root / "evidence" / "pages").glob("page-*.json"))),
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
