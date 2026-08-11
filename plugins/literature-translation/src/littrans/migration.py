from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rapidfuzz.fuzz import ratio

from littrans.evidence import (
    source_unit_fingerprint,
    structure_unit_fingerprint,
    translation_unit_fingerprint,
)
from littrans.extractor import parse_page_spec
from littrans.models import (
    AuditRun,
    ExternalReviewRun,
    ExternalReviewVerdict,
    ProjectStatus,
    ReviewScope,
    SourceUnit,
    TranslationRecord,
    UnitKind,
    utc_now,
)
from littrans.project import promote_status, translation_map
from littrans.storage import (
    append_jsonl,
    initialize_project_dirs,
    load_project,
    project_write_lock,
    read_json,
    read_jsonl,
    save_project,
    sha256_text,
    write_json,
    write_jsonl,
)


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


def _legacy_v3_batch_fingerprint(
    root: Path,
    batch_id: str,
    *,
    units: dict[str, SourceUnit] | None = None,
    translations: dict[str, TranslationRecord] | None = None,
) -> str:
    from littrans.batching import load_manifest

    manifest = load_manifest(root, batch_id)
    translations = translations if translations is not None else translation_map(root)
    units = (
        units
        if units is not None
        else {
            unit.unit_id: unit
            for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
        }
    )
    source_fields = {
        "kind",
        "source_hash",
        "source_markdown",
        "sidebar_id",
        "sidebar_role",
        "callout_kind",
        "translatable",
        "render_policy",
        "protected_tokens",
        "latex",
        "equation_number",
        "math_status",
        "table",
        "code_language",
        "continues_from_previous",
        "continued_to_next",
        "figure_labels",
        "verification_status",
    }
    material: list[str] = []
    for unit_id in manifest.unit_ids:
        unit = units[unit_id]
        semantic = unit.model_dump_json(
            include=source_fields, exclude_none=True
        )
        source_fingerprint = sha256_text(semantic)
        if not unit.translatable:
            material.append(f"{unit_id}|source-only|{source_fingerprint}")
            continue
        record = translations.get(unit_id)
        if record is None:
            material.append(f"{unit_id}|{source_fingerprint}|missing")
        else:
            translation_json = record.model_dump_json(
                include={
                    "target_text",
                    "target_table",
                    "figure_labels",
                    "reader_note",
                    "term_proposals",
                    "uncertainties",
                },
                exclude_none=True,
            )
            material.append(
                f"{unit_id}|{record.source_hash}|{source_fingerprint}|{record.revision}|"
                f"{sha256_text(translation_json)}"
            )
    return sha256_text("\n".join(material))


def _migratable_v3_external_chain(
    runs: list[ExternalReviewRun],
    legacy_fingerprint: str,
    packet_sha256: str | None = None,
) -> tuple[list[ExternalReviewRun], bool]:
    matching = [
        run
        for run in runs
        if run.translation_fingerprint == legacy_fingerprint
        and (packet_sha256 is None or run.packet_sha256 == packet_sha256)
    ]
    primary_index = next(
        (
            index
            for index in range(len(matching) - 1, -1, -1)
            if matching[index].role == "primary"
        ),
        None,
    )
    if primary_index is None:
        return [], bool(runs)

    primary = matching[primary_index]

    def accepted(run: ExternalReviewRun) -> bool:
        return bool(
            run.success
            and run.model_verified
            and run.verdict is ExternalReviewVerdict.ACCEPTED
        )

    if not accepted(primary):
        return [], True

    chain = [primary]
    second_candidates = [
        run
        for index, run in enumerate(matching)
        if run.role == "second-opinion"
        and (
            run.base_run_id == primary.run_id
            or (run.base_run_id is None and index > primary_index)
        )
    ]
    if second_candidates:
        second = second_candidates[-1]
        if accepted(second):
            chain.append(second)
            return chain, False
        return chain, True
    return chain, False


def migrate_project_schema(
    root: Path, to_version: int = 4, dry_run: bool = False
) -> dict[str, Any]:
    """Upgrade v3 projects without changing source, translations, issues, or approvals."""
    if to_version != 4:
        raise ValueError("Only project schema version 4 is supported")
    config = load_project(root)
    if config.schema_version > to_version:
        raise ValueError(
            f"Project schema {config.schema_version} is newer than requested {to_version}"
        )
    if config.schema_version == to_version:
        return {
            "project": str(root),
            "from": to_version,
            "to": to_version,
            "dry_run": dry_run,
            "changed": False,
            "message": "Project already uses schema v4.",
        }
    if config.schema_version != 3:
        raise ValueError(
            "Schema-v4 project migration requires a schema-v3 source project; "
            f"found schema {config.schema_version}"
        )

    from littrans.batching import load_manifest
    from littrans.external_review import (
        _external_review_context_fingerprint,
        _legacy_v3_packet_context,
        _legacy_v3_packet_text,
    )
    from littrans.quality import (
        current_qa_context_fingerprint,
    )

    batch_ids = sorted(
        path.name
        for path in (root / "batches").iterdir()
        if path.is_dir() and (path / "manifest.yaml").is_file()
    )
    manifests = {batch_id: load_manifest(root, batch_id) for batch_id in batch_ids}
    evidence_batch_ids = [
        batch_id
        for batch_id in batch_ids
        if (root / "qa" / f"{batch_id}.json").is_file()
        or (root / "reviews" / f"{batch_id}.audit.json").is_file()
        or (root / "reviews" / f"{batch_id}.external-runs.jsonl").is_file()
    ]
    all_units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    unit_map = {unit.unit_id: unit for unit in all_units}
    translations = translation_map(root)
    needed_unit_ids = {
        unit_id
        for batch_id in evidence_batch_ids
        for unit_id in manifests[batch_id].unit_ids
    }
    missing_units = sorted(needed_unit_ids - set(unit_map))
    if missing_units:
        raise ValueError(
            "Schema-v4 migration cannot fingerprint removed manifest units: "
            f"{missing_units}"
        )
    current_unit_fingerprints = {
        unit_id: translation_unit_fingerprint(
            unit_map[unit_id], translations.get(unit_id)
        )
        for unit_id in needed_unit_ids
    }
    source_unit_fingerprints = {
        unit_id: source_unit_fingerprint(unit_map[unit_id])
        for unit_id in needed_unit_ids
    }
    structure_unit_fingerprints = {
        unit_id: structure_unit_fingerprint(unit_map[unit_id])
        for unit_id in needed_unit_ids
    }
    legacy_packet_context = _legacy_v3_packet_context(root)
    candidates: dict[str, dict[str, Any]] = {}
    stale: dict[str, list[str]] = {"qa": [], "audit": [], "external": []}
    current_qa_context = current_qa_context_fingerprint(root)
    for batch_id in evidence_batch_ids:
        manifest = manifests[batch_id]
        legacy_fingerprint = _legacy_v3_batch_fingerprint(
            root,
            batch_id,
            units=unit_map,
            translations=translations,
        )
        unit_fingerprints = {
            unit_id: current_unit_fingerprints[unit_id]
            for unit_id in manifest.unit_ids
        }
        current_fingerprint = sha256_text(
            "\n".join(
                f"{unit_id}:{fingerprint}"
                for unit_id, fingerprint in unit_fingerprints.items()
            )
        )
        item: dict[str, Any] = {
            "legacy_fingerprint": legacy_fingerprint,
            "current_fingerprint": current_fingerprint,
            "unit_fingerprints": unit_fingerprints,
            "source_fingerprint": sha256_text(
                "\n".join(
                    f"{unit_id}:{source_unit_fingerprints[unit_id]}"
                    for unit_id in manifest.unit_ids
                )
            ),
            "structure_fingerprint": sha256_text(
                "\n".join(
                    f"{unit_id}:{structure_unit_fingerprints[unit_id]}"
                    for unit_id in manifest.unit_ids
                )
            ),
            "qa": None,
            "qa_context_verified": False,
            "audit_lenses": [],
            "audit_context_verified": False,
            "external_runs": [],
            "external_context_fingerprint": None,
            "unit_ids": manifest.unit_ids,
            "translatable_unit_ids": manifest.translatable_unit_ids,
        }
        qa_path = root / "qa" / f"{batch_id}.json"
        if qa_path.is_file():
            qa = read_json(qa_path)
            if qa.get("passed") and qa.get("translation_fingerprint") == legacy_fingerprint:
                item["qa"] = qa
                item["qa_context_verified"] = (
                    qa.get("qa_context_fingerprint") == current_qa_context
                )
                if not item["qa_context_verified"]:
                    stale["qa"].append(batch_id)
            else:
                stale["qa"].append(batch_id)
        audit_path = root / "reviews" / f"{batch_id}.audit.json"
        if audit_path.is_file():
            audit = read_json(audit_path)
            if audit.get("translation_fingerprint") == legacy_fingerprint:
                item["audit_lenses"] = [
                    lens
                    for lens in audit.get("lenses", [])
                    if lens in {"fidelity", "technical", "chinese-style"}
                ]
                if item["audit_lenses"]:
                    stale["audit"].append(batch_id)
            else:
                stale["audit"].append(batch_id)
        runs_path = root / "reviews" / f"{batch_id}.external-runs.jsonl"
        runs = read_jsonl(runs_path, ExternalReviewRun)
        if runs:
            packet_text, _ = _legacy_v3_packet_text(
                root,
                batch_id,
                _all_units=all_units,
                _translations=translations,
                _legacy_context=legacy_packet_context,
            )
            packet_sha256 = sha256_text(packet_text)
            external_chain, external_stale = _migratable_v3_external_chain(
                runs, legacy_fingerprint, packet_sha256
            )
        else:
            external_chain, external_stale = [], False
        item["external_runs"] = external_chain
        if external_chain:
            item["external_context_fingerprint"] = (
                _external_review_context_fingerprint(
                    root,
                    batch_id,
                    list(manifest.unit_ids),
                    ReviewScope.FULL,
                )
            )
        if external_stale:
            stale["external"].append(batch_id)
        candidates[batch_id] = item

    report: dict[str, Any] = {
        "project": str(root),
        "from": config.schema_version,
        "to": to_version,
        "dry_run": dry_run,
        "changed": not dry_run,
        "batches": len(batch_ids),
        "importable": {
            "qa": sum(
                item["qa"] is not None and item["qa_context_verified"]
                for item in candidates.values()
            ),
            "audit_lenses": sum(
                len(item["audit_lenses"])
                for item in candidates.values()
                if item["audit_context_verified"]
            ),
            "external_runs": sum(
                len(item["external_runs"]) for item in candidates.values()
            ),
        },
        "pending_recheck": stale,
    }
    if dry_run:
        return report

    # Seed page-level receipts while the migration lock is held. The private
    # legacy-schema bypass keeps the v4 marker as the final commit point.
    from littrans.verification import verify_extraction

    with project_write_lock(root):
        initialize_project_dirs(root)
        for batch_id, item in candidates.items():
            if item["qa"] is not None:
                qa = dict(item["qa"])
                qa["translation_fingerprint"] = item["current_fingerprint"]
                qa["schema_version"] = 2
                if item["qa_context_verified"]:
                    qa["qa_context_fingerprint"] = current_qa_context
                else:
                    qa.pop("qa_context_fingerprint", None)
                write_json(root / "qa" / f"{batch_id}.json", qa)
            audit_runs_path = root / "evidence" / "audits" / f"{batch_id}.jsonl"
            existing_ids = {
                run.run_id for run in read_jsonl(audit_runs_path, AuditRun)
            }
            for lens in item["audit_lenses"]:
                run_id = f"migrated-{batch_id}-{lens}"
                if run_id in existing_ids:
                    continue
                append_jsonl(
                    audit_runs_path,
                    [
                        AuditRun(
                            run_id=run_id,
                            batch_ids=[batch_id],
                            reviewer="v3-evidence-migration",
                            lens=lens,
                            scope=ReviewScope.FULL,
                            unit_fingerprints={
                                unit_id: item["unit_fingerprints"][unit_id]
                                for unit_id in item["unit_ids"]
                            },
                        )
                    ],
                )
            if item["audit_lenses"]:
                audit_path = root / "reviews" / f"{batch_id}.audit.json"
                audit = read_json(audit_path)
                audit["translation_fingerprint"] = item["current_fingerprint"]
                audit["historical_lenses"] = item["audit_lenses"]
                audit["lenses"] = []
                audit["unit_coverage"] = {
                    lens: []
                    for lens in item["audit_lenses"]
                }
                audit["missing_coverage"] = {
                    lens: item["unit_ids"]
                    for lens in {"fidelity", "technical", "chinese-style"}
                }
                write_json(audit_path, audit)
            runs_path = root / "reviews" / f"{batch_id}.external-runs.jsonl"
            latest_migrated_primary_id: str | None = None
            for old in item["external_runs"]:
                migrated_id = f"{old.run_id}-v4"
                migrated_base_run_id: str | None = old.run_id
                if old.role == "primary":
                    latest_migrated_primary_id = migrated_id
                elif old.base_run_id:
                    migrated_base_run_id = f"{old.base_run_id}-v4"
                else:
                    migrated_base_run_id = latest_migrated_primary_id
                if any(
                    run.run_id == migrated_id
                    for run in read_jsonl(runs_path, ExternalReviewRun)
                ):
                    continue
                append_jsonl(
                    runs_path,
                    [
                        old.model_copy(
                            update={
                                "schema_version": 2,
                                "run_id": migrated_id,
                                "translation_fingerprint": item[
                                    "current_fingerprint"
                                ],
                                "scope": ReviewScope.FULL,
                                "base_run_id": migrated_base_run_id,
                                "covered_unit_ids": item["unit_ids"],
                                "unit_fingerprints": item[
                                    "unit_fingerprints"
                                ],
                                "source_fingerprint": item[
                                    "source_fingerprint"
                                ],
                                "structure_fingerprint": item[
                                    "structure_fingerprint"
                                ],
                                "context_fingerprint": item[
                                    "external_context_fingerprint"
                                ],
                            }
                        )
                    ],
                )
        verification = verify_extraction(
            root, "all", force=True, _allow_legacy_schema=True
        )
        report["source_verification"] = {
            "passed": verification["passed"],
            "verified_pages": verification.get("verified_pages", []),
            "errors": verification.get("errors", []),
        }
        report["migrated_at"] = utc_now()
        write_json(root / "evidence" / "migration-v3-v4.json", report)
        config.schema_version = 4
        save_project(root, config)
    return report
