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
    qa_context_fingerprints: dict[str, str]


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
        qa_context_fingerprints={m.batch_id: current_qa_context_fingerprint(root, m.batch_id) for m in manifests},
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
    if (
        start_series is not None
        and through_series is not None
        and start_series != through_series
    ):
        raise ValueError("workflow resume bounds belong to different batch series")
    active_series = start_series or through_series
    if active_series is None:
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
    return _batch_stage_details(root, batch_id, snapshot, context_cache)[0]


def _batch_stage_details(
    root: Path,
    batch_id: str,
    snapshot: WorkflowSnapshot | None = None,
    context_cache: dict[tuple[str, ...], tuple[str, dict[str, str]] | None]
    | None = None,
) -> tuple[str, dict[str, list[str]]]:
    """Return the batch stage and, for the audit stage, why coverage is stale."""
    snapshot = snapshot or _load_workflow_snapshot(root)
    manifest = next(
        (item for item in snapshot.manifests if item.batch_id == batch_id), None
    )
    if manifest is None:
        raise ValueError(f"Unknown batch ID: {batch_id}")
    translations = snapshot.translations
    if any(unit_id not in translations for unit_id in manifest.translatable_unit_ids):
        return "translate", {}
    qa_report = snapshot.qa_reports[batch_id]
    if not (
        qa_report
        and qa_report.translation_fingerprint
        == _translation_fingerprint_from_snapshot(snapshot, manifest)
        and qa_report.qa_context_fingerprint == snapshot.qa_context_fingerprints[manifest.batch_id]
    ):
        return "qa", {}
    if not qa_report.passed:
        if qa_report.errors and all(error.code == "asset-semantic-uncertainty" for error in qa_report.errors):
            if _asset_lane(root, manifest, snapshot.unit_map)["recovery"]:
                return "transcribe", {}
        return "revise", {}
    coverage = audit_coverage(
        root,
        batch_id,
        manifest=manifest,
        all_units=snapshot.unit_map,
        translations=translations,
        runs=snapshot.audit_runs[batch_id],
        context_cache=context_cache,
    )
    if not coverage["complete"]:
        return "audit", {
            lens: reasons
            for lens, reasons in coverage["stale_reasons"].items()
            if reasons
        }
    stage = _post_audit_stage(snapshot, manifest, batch_id)
    return stage, {}


def _post_audit_stage(
    snapshot: WorkflowSnapshot, manifest: BatchManifest, batch_id: str
) -> str:
    translations = snapshot.translations
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


def _asset_lane(root: Path, manifest: BatchManifest, units: dict[str, SourceUnit]) -> dict[str, Any]:
    from littrans.fidelity_models import asset_reference_ids
    from littrans.representations import representation_status
    ids = list(dict.fromkeys(a for uid in manifest.unit_ids for a in asset_reference_ids(
        units[uid].source_markdown or units[uid].source_text
    )))
    status = representation_status(root, ids)
    states = status["assets"]
    pending = {name: [aid for aid, row in states.items() if row["state"] == name]
               for name in ("transcribe", "asset-audit")}
    recovery = [aid for aid, row in states.items() if row["state"] == "fallback" and row["semantic_uncertainty"]]
    if recovery:
        pending["transcribe"] = recovery  # Repair blocking evidence before fresh optional enhancement.
    return {"states": states, "pending": pending, "recovery": recovery, "complete": not any(pending.values())}


def _dispatch_stage(translation_stage: str, lane: dict[str, Any]) -> str:
    # Original images are a complete reading representation. Enhancement work
    # remains available independently, including after translation completion.
    return translation_stage


def _editable_revision_batches(root: Path, batch_ids: list[str], snapshot: WorkflowSnapshot) -> list[str]:
    """Resolve dependency-only QA failures to batches allowed to edit those units."""
    by_id = {m.batch_id: m for m in snapshot.manifests}

    def resolve(bid: str, visiting: set[str]) -> list[str]:
        if bid in visiting:
            raise ValueError(f"Cyclic dependency revision ownership at {bid}; refresh owning batches")
        report = snapshot.qa_reports[bid]
        if _batch_stage(root, bid, snapshot) != "revise" or report is None or report.passed:
            return [bid]
        editable = set(by_id[bid].translatable_unit_ids)
        # Repair local errors first; unlike foreign errors, this task can change them.
        if any(error.unit_id is None or error.unit_id in editable for error in report.errors):
            return [bid]
        foreign = list(dict.fromkeys(error.unit_id for error in report.errors if error.unit_id))
        owners: list[str] = []
        for uid in foreign:
            owner = next((m.batch_id for m in snapshot.manifests if uid in m.translatable_unit_ids), None)
            if owner is None:
                raise ValueError(f"QA dependency {uid} has no editable owning batch; create or refresh its batch before continuing")
            owners.extend(resolve(owner, visiting | {bid}))
        return list(dict.fromkeys(owners)) or [bid]

    return list(dict.fromkeys(owner for bid in batch_ids for owner in resolve(bid, set())))


def _ready_tasks(root: Path, batch_ids: list[str], snapshot: WorkflowSnapshot,
                 host: str, *, optional_assets: bool = False) -> list[dict[str, Any]]:
    config = load_project(root)
    model_policy = config.agent_models.get(host, {})
    tasks: list[dict[str, Any]] = []
    by_id = {m.batch_id: m for m in snapshot.manifests}
    if not optional_assets:
        batch_ids = _editable_revision_batches(root, batch_ids, snapshot)
    for bid in batch_ids:
        stage = _batch_stage(root, bid, snapshot)
        lane = _asset_lane(root, by_id[bid], snapshot.unit_map)
        if stage != "complete" and not optional_assets:
            # Revision is translator work: it reuses the translate model policy.
            role = "translate" if stage == "revise" else stage
            tasks.append({"batch_id": bid, "stage": stage, "depends_on": ["source-fidelity"],
                          "model": model_policy.get(role),
                          "reasoning_effort": model_policy.get("reasoning_effort") if role == "translate" else None,
                          "fresh_context": True})
            if stage == "transcribe" and lane["recovery"]:
                tasks[-1].update(asset_ids=lane["recovery"], recovery=True)
        for role, ids in lane["pending"].items():
            if ids and optional_assets:
                tasks.append({"batch_id": bid, "stage": role, "asset_ids": ids, "optional": True,
                              "depends_on": ["source-fidelity"] if role == "transcribe" else ["candidate"],
                              "model": model_policy.get(role),
                              "reasoning_effort": model_policy.get("reasoning_effort") if role == "transcribe" else None,
                              "fresh_context": True})
                if role == "transcribe" and lane["recovery"]:
                    tasks[-1]["recovery"] = True
    return tasks


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
    if not manifests:
        raise ValueError(
            "No batch manifests exist yet; run `batch create PROJECT --pages PAGES` "
            "on verified pages before workflow coordination"
        )
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
            if unit_id in unit_map and unit_map[unit_id].translatable and unit_id not in manifest.read_only_unit_ids
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
    stage_details = {
        manifest.batch_id: _batch_stage_details(root, manifest.batch_id, snapshot, context_cache)
        for manifest in manifests
    }
    stages = [
        (
            manifest.batch_id,
            _dispatch_stage(stage_details[manifest.batch_id][0],
                            _asset_lane(root, manifest, snapshot.unit_map)),
        )
        for manifest in manifests
    ]
    start = next((index for index, (_, stage) in enumerate(stages) if stage != "complete"), None)
    if start is None:
        pending = [m.batch_id for m in manifests
                   if not _asset_lane(root, m, snapshot.unit_map)["complete"]][:resolved_limit]
        return {
            "stage": "complete",
            "batch_ids": [],
            "ready_tasks": [],
            "optional_asset_tasks": _ready_tasks(root, pending, snapshot, resolved_host, optional_assets=True),
            "audit_stale": {},
            "schedule": "translation-first-optional-assets",
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
    requested_batch_ids = list(batch_ids)
    dispatched = _editable_revision_batches(root, batch_ids, snapshot)
    if dispatched != batch_ids:
        stage = _batch_stage(root, dispatched[0], snapshot)
        batch_ids = []
        for bid in dispatched:
            if _batch_stage(root, bid, snapshot) != stage or len(batch_ids) >= resolved_limit:
                break
            batch_ids.append(bid)
        stage_details.update({bid: _batch_stage_details(root, bid, snapshot, context_cache) for bid in batch_ids})
    return {
        "stage": stage,
        "batch_ids": batch_ids,
        "requested_batch_ids": requested_batch_ids,
        "host": resolved_host,
        "limit": resolved_limit,
        "start_at": start_at,
        "through": through,
        "ready_tasks": _ready_tasks(root, batch_ids, snapshot, resolved_host),
        "optional_asset_tasks": _ready_tasks(root, batch_ids, snapshot, resolved_host, optional_assets=True),
        "audit_stale": {
            batch_id: stage_details[batch_id][1]
            for batch_id in batch_ids
            if stage_details[batch_id][1]
        },
        "schedule": "translation-first-optional-assets",
    }


def workflow_status(root: Path, batch_ids: Iterable[str], host: str | None = None) -> dict[str, Any]:
    """Return a compact status for an already-selected wave."""
    require_current_project_schema(root, "Workflow coordination")
    requested = list(batch_ids)
    resolved_host = resolve_coordination_host(host)
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
            if snapshot.unit_map[unit_id].translatable and unit_id not in manifest.read_only_unit_ids
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
    stage_details = {
        batch_id: _batch_stage_details(root, batch_id, snapshot, context_cache)
        for batch_id in requested
    }
    stages = {batch_id: details[0] for batch_id, details in stage_details.items()}
    unique_stages = set(stages.values())
    lanes = {m.batch_id: _asset_lane(root, m, snapshot.unit_map) for m in requested_manifests}
    return {
        "batch_ids": requested,
        "host": resolved_host,
        "stage": next(iter(unique_stages)) if len(unique_stages) == 1 else "mixed",
        "stages": stages,
        "audit_stale": {
            batch_id: details[1] for batch_id, details in stage_details.items() if details[1]
        },
        "reading_complete": all(stage == "complete" for stage in stages.values()),
        "assets": lanes,
        "ready_tasks": _ready_tasks(root, requested, snapshot, resolved_host),
        "optional_asset_tasks": _ready_tasks(root, requested, snapshot, resolved_host, optional_assets=True),
        "assets_complete": all(x["complete"] for x in lanes.values()),
        "complete": all(stage == "complete" for stage in stages.values()),
    }


def _validate_batch_set(
    root: Path, batch_ids: list[str], *, label: str = "Workflow packet"
) -> list[Any]:
    """Validate one batch set for a packet or a render.

    Batches may come from different series (page-range or prefix lineages) as
    long as their units do not overlap and they follow source order; within a
    series the selected batches must stay consecutive and ordered so a historic
    re-batched lineage cannot interleave with an active one.
    """
    if not 1 <= len(batch_ids) <= WAVE_BATCH_SET_MAX:
        raise ValueError(f"{label} sets require 1 to {WAVE_BATCH_SET_MAX} batch IDs")
    if len(set(batch_ids)) != len(batch_ids):
        raise ValueError(f"{label} batch IDs must be unique")
    ordered = _all_manifests(root)
    all_by_id = {manifest.batch_id: manifest for manifest in ordered}
    missing = [batch_id for batch_id in batch_ids if batch_id not in all_by_id]
    if missing:
        raise ValueError(f"Unknown batch IDs: {missing}")
    requested_unit_counts = Counter(
        unit_id for batch_id in batch_ids for unit_id in all_by_id[batch_id].unit_ids
    )
    requested_overlaps = sorted(
        unit_id for unit_id, count in requested_unit_counts.items() if count > 1
    )
    if requested_overlaps:
        raise ValueError(
            f"{label} batches contain overlapping source units: {requested_overlaps}"
        )
    by_series: dict[str | None, list[str]] = {}
    for batch_id in batch_ids:
        by_series.setdefault(_batch_series(batch_id), []).append(batch_id)
    for series, series_batch_ids in by_series.items():
        lineage = (
            ordered
            if series is None
            else [m for m in ordered if _batch_series(m.batch_id) == series]
        )
        index = {manifest.batch_id: position for position, manifest in enumerate(lineage)}
        positions = [index[batch_id] for batch_id in series_batch_ids]
        if positions != list(range(min(positions), min(positions) + len(positions))):
            raise ValueError(
                f"{label} batch IDs must be consecutive and ordered within series "
                f"{series or '(none)'}: {series_batch_ids}"
            )
    selected = [all_by_id[batch_id] for batch_id in batch_ids]
    current_units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    current_unit_map = {unit.unit_id: unit for unit in current_units}
    current_unit_ids = set(current_unit_map)
    unit_positions = {unit.unit_id: index for index, unit in enumerate(current_units)}
    first_positions = [
        min(unit_positions.get(unit_id, len(current_units)) for unit_id in manifest.unit_ids)
        for manifest in selected
    ]
    if first_positions != sorted(first_positions):
        raise ValueError(f"{label} batch IDs must follow source order: {batch_ids}")
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
            if current_unit_map[unit_id].translatable and unit_id not in manifest.read_only_unit_ids
        ]
    ]
    if stale_translatability:
        raise ValueError(
            "Workflow manifests have stale translatable-unit scope; refresh the "
            "affected batches before creating a packet: "
            f"batch_ids={stale_translatability}"
        )
    return selected


def _shared_context(root: Path, units: list[SourceUnit]) -> str:
    return audit_context_text(root, units)


def _audit_unit_text(unit: SourceUnit, record: TranslationRecord | None) -> str:
    source = (
        equation_markdown(unit)
        if unit.kind is UnitKind.EQUATION and "{{asset:" not in (unit.source_markdown or unit.source_text)
        else unit.source_markdown or unit.source_text
    )
    if unit.table:
        source = "\n".join(" | ".join(row) for row in unit.table.rows)
    if unit.figure_labels:
        source += "\n\nFigure label sources:\n" + "\n".join(
            f"- {label.source}" for label in unit.figure_labels
        )
    # Historic records for a now source-only formula are not translation content.
    if not unit.translatable:
        record = None
    target = record.target_text if record else (
        "[source-only] non-translatable unit: the original image is the reading content; "
        "no translation is expected and its absence is not an omission"
        if not unit.translatable else "[source-only]"
    )
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
    if record and record.asset_translations:
        target += "\n\nImage-contained language translations (verify any language_present=false claim against the original):\n" + json.dumps(
            [a.model_dump(mode="json") for a in record.asset_translations], ensure_ascii=False,
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
    from littrans.context_packets import TARGET_TEXT_CONTRACTS

    return (
        f"# {lens} audit: {batch_id}\n\n{focus}\n\n"
        "Return ReviewIssue JSONL; empty means no issues.\n\n"
        f"{TARGET_TEXT_CONTRACTS}\n\n"
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


def _jsonl_text(records: Iterable[Any]) -> str:
    return "".join(
        json.dumps(record.model_dump(mode="json", exclude_none=True), ensure_ascii=False) + "\n"
        for record in records
    )


def _revise_packet_text(
    batch_id: str, open_issues: list[ReviewIssue], qa_report: QAReport | None
) -> str:
    from littrans.context_packets import TARGET_TEXT_CONTRACTS

    lines = [
        f"# Revision: {batch_id}",
        "",
        f"Current translation records are in {batch_id}.translation.jsonl; open review "
        f"issues are in {batch_id}.issues.jsonl. Read the source, the original images "
        "and every open issue before revising.",
        "",
        "1. Address every open issue below in its unit, then sweep the whole batch for the "
        "same defect class so consistency does not depend on the reviewer having listed "
        "every instance.",
        "2. Resubmit the full batch with `translation submit` (unchanged records may be "
        "resubmitted verbatim) and run `qa run` until it passes.",
        "3. Report the issue ids you addressed and, separately, any you deliberately left "
        "unchanged with the reason; the coordinator resolves them with `review resolve`. "
        "Do not resolve issues yourself.",
        "",
        TARGET_TEXT_CONTRACTS,
        "",
        "## Open issues",
        "",
    ]
    if not open_issues:
        lines.append("None open; revise only for the QA errors below.")
    for issue in open_issues:
        lines.extend(
            [
                f"### {issue.issue_id} ({issue.severity}; {issue.type}; unit {issue.unit_id})",
                "",
                issue.explanation,
            ]
        )
        if issue.source_span:
            lines.append(f"Source span: {issue.source_span}")
        if issue.target_span:
            lines.append(f"Target span: {issue.target_span}")
        if issue.suggested_revision:
            lines.append(f"Suggested revision: {issue.suggested_revision}")
        lines.append("")
    if qa_report is not None and not qa_report.passed:
        lines.extend(["## Current QA errors", ""])
        lines.extend(
            f"- {item.code} ({item.unit_id or 'batch'}): {item.message}"
            for item in qa_report.errors
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def create_workflow_packet(
    root: Path,
    stage: str,
    batch_ids: list[str],
    lens: str | None = None,
    host: str | None = None,
) -> WorkflowPacketManifest | list[WorkflowPacketManifest] | dict[str, Any]:
    require_current_project_schema(root, "Workflow packet creation")
    host = resolve_coordination_host(host)
    if stage in {"transcribe", "asset-audit"}:
        if lens is not None:
            raise ValueError("Asset tasks do not accept a translation audit lens")
        from littrans.fidelity_models import asset_reference_ids
        from littrans.representations import build_asset_packet, representation_status
        asset_manifests = _validate_batch_set(root, batch_ids)
        scope = {uid for m in asset_manifests for uid in m.unit_ids}
        context_units = [u for u in read_jsonl(root / "derived/units.jsonl", SourceUnit) if u.unit_id in scope]
        ids = list(dict.fromkeys(a for u in context_units for a in asset_reference_ids(u.source_markdown or u.source_text)))
        states = representation_status(root, ids)["assets"]
        recovery = [aid for aid in ids if states[aid]["state"] == "fallback" and states[aid]["semantic_uncertainty"]]
        revision_notes = None
        if stage == "transcribe" and recovery:
            ids = recovery
            revision_notes = "Resolve the independent review's semantic uncertainty: " + "; ".join(
                f"{aid}: {states[aid]['semantic_uncertainty']}" for aid in recovery)
        else:
            ids = [aid for aid in ids if states[aid]["state"] == stage]
        if not ids:
            return {"stage": stage, "batch_ids": batch_ids, "asset_ids": [], "pending": False}
        from littrans.context_packets import adjacent_source_units
        return build_asset_packet(root, ids, stage=stage, context_units=context_units + adjacent_source_units(root, context_units),
                                  revision_notes=revision_notes, host=host)
    if stage not in {"translate", "revise", "audit"}:
        raise ValueError("workflow packet stage must be translate, revise or audit")
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
            packet = create_workflow_packet(root, stage, batch_ids, selected_lens, host)
            if isinstance(packet, list):  # pragma: no cover - guarded above
                packets.extend(packet)
            elif isinstance(packet, WorkflowPacketManifest):
                packets.append(packet)
        return packets
    if stage == "audit" and lens not in REQUIRED_AUDIT_LENSES:
        raise ValueError(
            "audit packets require --lens all|fidelity|technical|chinese-style"
        )
    if stage in {"translate", "revise"} and lens is not None:
        raise ValueError("translation packets do not accept a lens")
    policy = load_project(root).agent_models.get(host, {})
    if stage in {"translate", "revise"}:
        if not policy.get("translate") or not policy.get("reasoning_effort"):
            raise ValueError(f"Configure agent_models.{host}.translate and reasoning_effort before creating translation tasks; no model substitution is allowed")
    selected_model = policy.get("translate" if stage in {"translate", "revise"} else "audit")
    selected_effort = policy.get("reasoning_effort") if stage in {"translate", "revise"} else None
    manifests = _validate_batch_set(root, batch_ids)
    all_units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    unit_map = {unit.unit_id: unit for unit in all_units}
    positions = {unit.unit_id: index for index, unit in enumerate(all_units)}
    translations = translation_map(root)
    if stage == "revise":
        untranslated = [
            manifest.batch_id
            for manifest in manifests
            if any(unit_id not in translations for unit_id in manifest.translatable_unit_ids)
        ]
        if untranslated:
            raise ValueError(
                "Revision packets require submitted translations for batches "
                f"{untranslated}; create a translate packet instead"
            )
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
    from littrans.context_packets import original_context
    original = original_context(root, selected_units, stage, include_adjacent=True)
    planned_files["original-images"] = (
        "original-images.json", json.dumps(original, ensure_ascii=False, indent=2) + "\n",
    )
    for manifest in manifests:
        batch_units = [
            unit_map[unit_id]
            for unit_id in batch_unit_ids[manifest.batch_id]
            if unit_id in unit_map
        ]
        if stage in {"translate", "revise"}:
            planned_files[f"{manifest.batch_id}:source"] = (
                f"{manifest.batch_id}.source.md",
                "Submit exactly these editable unit IDs: " + ", ".join(manifest.translatable_unit_ids)
                + "\nRead-only context unit IDs (do not submit): " + (", ".join(manifest.read_only_unit_ids) or "none")
                + "\n\n" + batch_source_markdown(root, batch_units),
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
            if manifest.read_only_unit_ids:
                context.extend(["# Current read-only group context (not a new approval)", "",
                    *[_audit_unit_text(unit_map[uid], translations.get(uid)) for uid in manifest.read_only_unit_ids]])
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
            if stage == "revise":
                open_issues = [
                    issue
                    for issue in read_jsonl(
                        root / "reviews" / f"{manifest.batch_id}.issues.jsonl", ReviewIssue
                    )
                    if issue.status is IssueStatus.OPEN
                ]
                qa_path = root / "qa" / f"{manifest.batch_id}.json"
                qa_report = (
                    QAReport.model_validate(read_json(qa_path)) if qa_path.is_file() else None
                )
                planned_files[f"{manifest.batch_id}:translation"] = (
                    f"{manifest.batch_id}.translation.jsonl",
                    _jsonl_text(
                        translations[unit_id]
                        for unit_id in manifest.translatable_unit_ids
                        if unit_id in translations
                    ),
                )
                planned_files[f"{manifest.batch_id}:issues"] = (
                    f"{manifest.batch_id}.issues.jsonl",
                    _jsonl_text(open_issues),
                )
                planned_files[f"{manifest.batch_id}:revise"] = (
                    f"{manifest.batch_id}.revise.md",
                    _revise_packet_text(manifest.batch_id, open_issues, qa_report),
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
                "version": 4,
                "stage": stage,
                "lens": lens,
                "host": host,
                "model": selected_model,
                "reasoning_effort": selected_effort,
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
        host=host,
        model=selected_model,
        reasoning_effort=selected_effort,
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
        issues.append(
            issue.model_copy(
                update={
                    "issue_id": canonical_id,
                    "source_issue_id": issue.issue_id if canonicalize_ids else issue.source_issue_id,
                }
            )
        )
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
    known_manifests = {
        manifest.batch_id: manifest for manifest in _all_manifests(root)
    }
    known = set(known_manifests)
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
            packet_batches_resolved = all(
                batch_id in known_manifests for batch_id in manifest.batch_ids
            ) and (
                not manifest.batch_unit_ids
                or set(manifest.batch_unit_ids) == set(manifest.batch_ids)
            )
            batch_coverage = {
                batch_id: (
                    manifest.batch_unit_ids.get(batch_id, [])
                    if manifest.batch_unit_ids
                    else [
                        unit_id
                        for unit_id in known_manifests[batch_id].unit_ids
                        if unit_id in manifest.unit_ids
                    ]
                )
                for batch_id in manifest.batch_ids
                if batch_id in known_manifests
            }
            required_batches = [
                batch_id
                for batch_id in manifest.batch_ids
                if batch_coverage.get(batch_id)
            ]
            expected_fingerprints = {
                batch_id: {
                    unit_id: manifest.unit_fingerprints[unit_id]
                    for unit_id in batch_coverage[batch_id]
                    if unit_id in manifest.unit_fingerprints
                }
                for batch_id in required_batches
            }
            fingerprints_complete = all(
                all(
                    unit_id in manifest.unit_fingerprints
                    for unit_id in batch_coverage[batch_id]
                )
                for batch_id in required_batches
            )
            completely_imported = (
                manifest.stage == "audit"
                and packet_batches_resolved
                and bool(required_batches)
                and fingerprints_complete
                and len(expected_fingerprints) == len(required_batches)
                and all(
                any(
                    run.packet_id == manifest.packet_id
                    and run.lens == manifest.lens
                    and all(
                        run.unit_fingerprints.get(unit_id) == fingerprint
                        for unit_id, fingerprint in expected_fingerprints[batch_id].items()
                    )
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
            (root / "evidence" / "pages" / f"fidelity-p{page:04}.review.json").is_file()
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
