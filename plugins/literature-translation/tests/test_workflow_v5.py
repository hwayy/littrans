from __future__ import annotations

from pathlib import Path

import pytest
from test_efficiency_v4 import _make_project, _submit

import littrans.workflow as workflow_module
from littrans.batching import refresh_batch
from littrans.models import IssueType, ReviewIssue, Severity, SourceUnit
from littrans.project import translation_map
from littrans.quality import audit_coverage, import_review, run_qa
from littrans.storage import read_jsonl, write_jsonl
from littrans.workflow import (
    create_workflow_packet,
    import_review_set,
    prune_workflow_packets,
    workflow_status,
)


def test_lens_all_skips_covered_lens_and_never_emits_empty_batch_run(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=2, max_words=100)
    first, second = manifests
    for batch in manifests:
        _submit(root, batch.batch_id)
        assert run_qa(root, batch.batch_id).passed

    covered = root / "reviews" / "covered.jsonl"
    write_jsonl(covered, [])
    import_review(root, first.batch_id, covered, lenses=["fidelity"])

    packets = create_workflow_packet(
        root, "audit", [first.batch_id, second.batch_id], "all"
    )
    assert isinstance(packets, list)
    fidelity = next(packet for packet in packets if packet.lens == "fidelity")
    assert fidelity.batch_ids == [second.batch_id]
    assert first.batch_id not in fidelity.batch_unit_ids


def test_wave_status_is_compact_and_packet_is_content_addressed(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, pages=2, max_words=100)
    batch_ids = [batch.batch_id for batch in manifests]
    assert workflow_status(root, batch_ids)["stages"] == {
        batch_id: "translate" for batch_id in batch_ids
    }
    first = create_workflow_packet(root, "translate", batch_ids)
    second = create_workflow_packet(root, "translate", batch_ids)
    assert not isinstance(first, list) and not isinstance(second, list)
    assert first.packet_id == second.packet_id
    assert first.storage_root == ".littrans/work"
    assert not any((root / "packets").iterdir())
    assert (root / ".gitignore").read_text(encoding="utf-8").splitlines().count(
        "/.littrans/"
    ) == 1


def test_import_returns_stable_id_map_and_prune_requires_imported_packet(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=1)
    batch = manifests[0]
    _submit(root, batch.batch_id)
    assert run_qa(root, batch.batch_id).passed
    packet = create_workflow_packet(root, "audit", [batch.batch_id], "fidelity")
    assert not isinstance(packet, list)
    issues = root / "reviews" / "empty-v5.jsonl"
    write_jsonl(issues, [])
    result = import_review_set(
        root,
        root / packet.storage_root / packet.packet_id / "manifest.json",
        issues,
    )
    assert result["id_map"] == {}
    dry_run = prune_workflow_packets(root)
    assert packet.packet_id in dry_run["candidates"]
    assert dry_run["removed"] == []


def test_packet_issue_ids_are_stable_on_idempotent_import(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, pages=1)
    batch = manifests[0]
    _submit(root, batch.batch_id)
    assert run_qa(root, batch.batch_id).passed
    packet = create_workflow_packet(root, "audit", [batch.batch_id], "fidelity")
    assert not isinstance(packet, list)
    issues = root / "reviews" / "one-local-issue.jsonl"
    write_jsonl(
        issues,
        [
            ReviewIssue(
                issue_id="issue-1",
                batch_id=batch.batch_id,
                unit_id=batch.unit_ids[0],
                severity=Severity.MINOR,
                type=IssueType.MEANING,
                explanation="A precise local finding.",
                reviewer="independent-fidelity-auditor",
            )
        ],
    )
    manifest_path = root / packet.storage_root / packet.packet_id / "manifest.json"
    first = import_review_set(root, manifest_path, issues)
    second = import_review_set(root, manifest_path, issues)
    assert first["id_map"] == second["id_map"]
    canonical = first["id_map"][f"{batch.batch_id}:issue-1"]
    assert canonical.startswith("audit-")
    stored = read_jsonl(
        root / "reviews" / f"{batch.batch_id}.issues.jsonl", ReviewIssue
    )
    assert [issue.issue_id for issue in stored] == [canonical]


def test_non_seam_change_invalidates_only_its_batch(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, pages=3, max_words=100)
    for batch in manifests:
        _submit(root, batch.batch_id)
        assert run_qa(root, batch.batch_id).passed
        for lens in ("fidelity", "technical", "chinese-style"):
            packet = create_workflow_packet(root, "audit", [batch.batch_id], lens)
            assert not isinstance(packet, list)
            issues = root / "reviews" / f"{batch.batch_id}-{lens}.jsonl"
            write_jsonl(issues, [])
            import_review_set(
                root,
                root / packet.storage_root / packet.packet_id / "manifest.json",
                issues,
            )
    changed = manifests[1]
    _submit(root, changed.batch_id, suffix="局部修改")
    assert not audit_coverage(root, changed.batch_id)["complete"]
    assert audit_coverage(root, manifests[0].batch_id)["complete"]
    assert audit_coverage(root, manifests[2].batch_id)["complete"]


def test_cross_batch_seam_enters_only_the_local_dependency_context(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, pages=3, max_words=100)
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    units[0] = units[0].model_copy(update={"continued_to_next": True})
    units[1] = units[1].model_copy(update={"continues_from_previous": True})
    write_jsonl(units_path, units)
    for batch in manifests:
        refresh_batch(root, batch.batch_id)
        _submit(root, batch.batch_id)
        assert run_qa(root, batch.batch_id).passed
    packet = create_workflow_packet(
        root, "audit", [manifests[0].batch_id], "fidelity"
    )
    assert not isinstance(packet, list)
    context = packet.batch_context_unit_ids[manifests[0].batch_id]
    assert units[0].unit_id in context
    assert units[1].unit_id in context
    assert units[2].unit_id not in context


def test_next_and_status_load_translations_once_per_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifests = _make_project(tmp_path, pages=3, max_words=100)
    calls = 0

    def counted(project: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return translation_map(project)

    monkeypatch.setattr(workflow_module, "translation_map", counted)
    workflow_module.workflow_next(
        root, start_at=manifests[1].batch_id, through=manifests[2].batch_id
    )
    assert calls == 1
    workflow_status(root, [batch.batch_id for batch in manifests])
    assert calls == 2
