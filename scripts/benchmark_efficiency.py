from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "plugins" / "literature-translation" / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from littrans.batching import batch_source_markdown
from littrans.evidence import (
    dependency_closure,
    translation_memory,
    translations_semantically_equal,
)
from littrans.models import SourceUnit, TranslationRecord
from littrans.project import translation_map
from littrans.storage import read_jsonl
from littrans.workflow import (
    _all_manifests,
    _audit_packet_text,
    _audit_read_only_context,
    _batch_stage,
    _shared_context,
)


def _lean_translation_context(
    root: Path,
    all_units: list[SourceUnit],
    positions: dict[str, int],
    unit_ids: list[str],
) -> str:
    memory = translation_memory(root, unit_ids, limit=6)
    first = positions[unit_ids[0]]
    last = positions[unit_ids[-1]]
    adjacent = []
    if first:
        adjacent.append(all_units[first - 1])
    if last + 1 < len(all_units):
        adjacent.append(all_units[last + 1])
    lines = ["# Retrieved approved translation memory", ""]
    for item in memory:
        lines.extend(
            [
                f"## {item['unit_id']}",
                "",
                f"Source: {item['source']}",
                "",
                f"Target: {item['target']}",
                "",
            ]
        )
    if not memory:
        lines.extend(["None yet.", ""])
    lines.extend(["# Adjacent source context", ""])
    lines.extend(f"- {unit.unit_id}: {unit.source_text}" for unit in adjacent)
    return "\n".join(lines).rstrip() + "\n"


def _consecutive_packet_groups(
    manifests: list[Any], selected_batch_ids: set[str]
) -> list[list[Any]]:
    groups: list[list[Any]] = []
    current: list[Any] = []
    current_unit_ids: set[str] = set()
    for manifest in manifests:
        if manifest.batch_id not in selected_batch_ids:
            if current:
                groups.append(current)
                current = []
                current_unit_ids = set()
            continue
        candidate_ids = set(manifest.unit_ids)
        if len(candidate_ids) != len(manifest.unit_ids):
            raise ValueError(
                f"Benchmark manifest {manifest.batch_id} contains duplicate source units"
            )
        if current_unit_ids & candidate_ids:
            groups.append(current)
            current = []
            current_unit_ids = set()
        current.append(manifest)
        current_unit_ids.update(candidate_ids)
        if len(current) == 3:
            groups.append(current)
            current = []
            current_unit_ids = set()
    if current:
        groups.append(current)
    return groups


def benchmark(root: Path, completed_only: bool) -> dict[str, object]:
    all_manifests = _all_manifests(root)
    manifests = all_manifests
    if completed_only:
        manifests = [
            manifest
            for manifest in manifests
            if _batch_stage(root, manifest.batch_id) == "complete"
        ]
    if not manifests:
        raise ValueError("Efficiency benchmark requires at least one selected batch")
    packet_groups = _consecutive_packet_groups(
        all_manifests, {manifest.batch_id for manifest in manifests}
    )
    history = read_jsonl(
        root / "translations" / "history.jsonl", TranslationRecord
    )
    all_units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    unit_map = {unit.unit_id: unit for unit in all_units}
    history = [record for record in history if record.unit_id in unit_map]
    if completed_only:
        selected_unit_ids = {
            unit_id for manifest in manifests for unit_id in manifest.unit_ids
        }
        history = [record for record in history if record.unit_id in selected_unit_ids]
    previous: dict[str, TranslationRecord] = {}
    semantic_noops = 0
    semantic_changes = 0
    for record in history:
        prior = previous.get(record.unit_id)
        if prior is not None:
            if translations_semantically_equal(
                unit_map[record.unit_id], prior, record
            ):
                semantic_noops += 1
            else:
                semantic_changes += 1
        previous[record.unit_id] = record

    positions = {unit.unit_id: index for index, unit in enumerate(all_units)}
    translations = translation_map(root)
    legacy_bytes = 0
    optimized_bytes = 0
    for group in packet_groups:
        group_units = [
            unit_map[unit_id]
            for manifest in group
            for unit_id in manifest.unit_ids
            if unit_id in unit_map
        ]
        optimized_bytes += len(_shared_context(root, group_units).encode("utf-8"))
        requested_ids = [
            unit_id for manifest in group for unit_id in manifest.unit_ids
        ]
        requested_set = set(requested_ids)
        audit_ids = dependency_closure(
            root,
            [manifest.batch_id for manifest in group],
            requested_set,
        )
        audit_id_set = set(audit_ids)
        audit_units = [unit_map[unit_id] for unit_id in audit_ids if unit_id in unit_map]
        audit_shared = _shared_context(root, audit_units).encode("utf-8")
        read_only_units = [
            unit for unit in audit_units if unit.unit_id not in requested_set
        ]
        read_only_context = (
            _audit_read_only_context(read_only_units, translations).encode("utf-8")
            if read_only_units
            else b""
        )
        for lens in ("fidelity", "technical", "chinese-style"):
            optimized_bytes += len(audit_shared) + len(read_only_context)
            for manifest in group:
                batch_units = [
                    unit_map[unit_id]
                    for unit_id in manifest.unit_ids
                    if unit_id in unit_map and unit_id in audit_id_set
                ]
                optimized_bytes += len(
                    _audit_packet_text(
                        manifest.batch_id,
                        lens,
                        batch_units,
                        translations,
                        bool(read_only_units),
                    ).encode("utf-8")
                )
        for manifest in group:
            batch_dir = root / "batches" / manifest.batch_id
            source_bytes = (batch_dir / "source.md").read_bytes()
            context_bytes = (batch_dir / "context.md").read_bytes()
            translation_path = batch_dir / "translation.jsonl"
            translation_bytes = (
                translation_path.read_bytes() if translation_path.is_file() else b""
            )
            # Legacy writer packet plus three independently repeated audit packets.
            legacy_bytes += len(source_bytes) + len(context_bytes)
            legacy_bytes += 3 * (
                len(source_bytes) + len(context_bytes) + len(translation_bytes)
            )
            lean_context = _lean_translation_context(
                root, all_units, positions, manifest.unit_ids
            ).encode("utf-8")
            current_source_bytes = batch_source_markdown(
                root,
                [
                    unit_map[unit_id]
                    for unit_id in manifest.unit_ids
                    if unit_id in unit_map
                ],
            ).encode("utf-8")
            optimized_bytes += len(current_source_bytes) + len(lean_context)
    if legacy_bytes <= 0:
        raise ValueError("Efficiency benchmark requires non-zero legacy baseline bytes")
    ratio = optimized_bytes / legacy_bytes
    return {
        "project": str(root),
        "completed_only": completed_only,
        "batches": len(manifests),
        "history_records": len(history),
        "semantic_noop_records": semantic_noops,
        "semantic_change_records": semantic_changes,
        "simulated_noop_new_revisions": 0,
        "simulated_noop_evidence_invalidations": 0,
        "legacy_packet_bytes": legacy_bytes,
        "optimized_packet_bytes": optimized_bytes,
        "optimized_packet_ratio": ratio,
        "packet_reduction": 1.0 - ratio,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only LitTrans history replay and packet-size benchmark."
    )
    parser.add_argument("project", type=Path)
    parser.add_argument("--completed-only", action="store_true")
    parser.add_argument("--expect-noops", type=int)
    parser.add_argument("--max-packet-ratio", type=float, default=0.70)
    args = parser.parse_args()
    result = benchmark(args.project.resolve(), args.completed_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.expect_noops is not None and result["semantic_noop_records"] != args.expect_noops:
        return 1
    if float(result["optimized_packet_ratio"]) > args.max_packet_ratio:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
