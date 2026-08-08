from __future__ import annotations

import json
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from littrans import external_review
from littrans.batching import create_batches, load_manifest
from littrans.evidence import (
    batch_source_fingerprint,
    batch_structure_fingerprint,
    batch_unit_fingerprints,
    translation_memory,
)
from littrans.external_review import _primary_review_scope
from littrans.migration import _legacy_v3_batch_fingerprint, migrate_project_schema
from littrans.models import (
    ExternalReviewConfig,
    ExternalReviewerConfig,
    ExternalReviewRun,
    ExternalReviewVerdict,
    ExtractionIssue,
    IssueStatus,
    ProjectStatus,
    ReviewScope,
    SemanticStatus,
    Severity,
    SourceUnit,
    TranslationRecord,
    UnitKind,
)
from littrans.project import initialize_project, translation_map
from littrans.quality import approve_batch, audit_coverage, import_review, run_qa
from littrans.rendering import render_project
from littrans.storage import (
    append_jsonl,
    load_project,
    read_json,
    read_jsonl,
    save_project,
    sha256_text,
    write_json,
    write_jsonl,
    write_yaml,
)
from littrans.translation import submit_translation
from littrans.verification import require_verified_extraction, verify_extraction
from littrans.workflow import create_workflow_packet, import_review_set


def test_claude_stdin_delivery_remains_shadow_gated() -> None:
    assert external_review.CLAUDE_STDIN_PROMPT_DELIVERY_ENABLED is False


def _make_project(tmp_path: Path, pages: int = 3) -> tuple[Path, list[object]]:
    pdf = tmp_path / "source.pdf"
    drawing = canvas.Canvas(str(pdf), pagesize=letter)
    units: list[SourceUnit] = []
    vocabulary = "architecture framework binding property control layout event style template"
    source = " ".join([vocabulary] * 14)
    for page in range(1, pages + 1):
        text = drawing.beginText(54, 740)
        for line in [" ".join(source.split()[offset : offset + 12]) for offset in range(0, 126, 12)]:
            text.textLine(line)
        drawing.drawText(text)
        drawing.showPage()
        units.append(
            SourceUnit(
                unit_id=f"u{page:03}",
                kind=UnitKind.PARAGRAPH,
                page=page,
                bbox=(45, 40, 560, 760),
                source_text=source,
                source_hash=sha256_text(source),
                verification_status=SemanticStatus.VERIFIED,
                confidence=1.0,
            )
        )
    drawing.save()
    root = tmp_path / "project"
    initialize_project(pdf, root, "technical-book", "Efficiency Fixture")
    write_jsonl(root / "derived" / "units.jsonl", units)
    assert verify_extraction(root, "all", force=True)["passed"]
    manifests = create_batches(root, "all", max_words=100, prefix="v4")
    assert len(manifests) == pages
    return root, manifests


def _submit(root: Path, batch_id: str, suffix: str = "") -> list[TranslationRecord]:
    manifest = load_manifest(root, batch_id)
    units = {
        unit.unit_id: unit
        for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    }
    records = [
        TranslationRecord(
            unit_id=unit_id,
            target_text="这是经过技术审校的中文译文" + suffix,
            source_hash=units[unit_id].source_hash,
        )
        for unit_id in manifest.translatable_unit_ids
    ]
    path = root / "batches" / batch_id / "input.jsonl"
    write_jsonl(path, records)
    return submit_translation(root, batch_id, path)


def _audit_and_approve(root: Path, batch_id: str) -> None:
    assert run_qa(root, batch_id).passed
    empty = root / "reviews" / f"{batch_id}.empty.jsonl"
    write_jsonl(empty, [])
    import_review(root, batch_id, empty)
    assert approve_batch(root, batch_id, "machine") is ProjectStatus.MACHINE_REVIEWED


def test_semantic_noop_changes_nothing(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _audit_and_approve(root, batch_id)
    current_path = root / "translations" / "current.jsonl"
    history_path = root / "translations" / "history.jsonl"
    before = {
        "current": current_path.read_bytes(),
        "history": history_path.read_bytes(),
        "project": (root / "project.yaml").read_bytes(),
        "audit": (root / "reviews" / f"{batch_id}.audit.json").read_bytes(),
        "runs": (root / "evidence" / "audits" / f"{batch_id}.jsonl").read_bytes(),
    }
    records = read_jsonl(current_path, TranslationRecord)
    noop = [
        record.model_copy(
            update={
                "revision": record.revision + 99,
                "status": ProjectStatus.DRAFT,
                "updated_at": "2000-01-01T00:00:00+00:00",
            }
        )
        for record in records
    ]
    input_path = root / "batches" / batch_id / "noop.jsonl"
    write_jsonl(input_path, noop)
    returned = submit_translation(root, batch_id, input_path)
    assert returned[0].revision == records[0].revision
    assert returned[0].status is ProjectStatus.MACHINE_REVIEWED
    assert before == {
        "current": current_path.read_bytes(),
        "history": history_path.read_bytes(),
        "project": (root / "project.yaml").read_bytes(),
        "audit": (root / "reviews" / f"{batch_id}.audit.json").read_bytes(),
        "runs": (root / "evidence" / "audits" / f"{batch_id}.jsonl").read_bytes(),
    }


def test_renderer_owned_caption_separator_is_semantic_noop(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    unit = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)[0]
    input_path = root / "batches" / batch_id / "caption-initial.jsonl"
    write_jsonl(
        input_path,
        [
            TranslationRecord(
                unit_id=unit.unit_id,
                target_text="图 1-1。标题",
                source_hash=unit.source_hash,
            )
        ],
    )
    submit_translation(root, batch_id, input_path)
    before = (root / "translations" / "history.jsonl").read_bytes()
    current = read_jsonl(
        root / "translations" / "current.jsonl", TranslationRecord
    )[0]
    input_path = root / "batches" / batch_id / "caption-noop.jsonl"
    write_jsonl(
        input_path,
        [current.model_copy(update={"target_text": "图 1-1 标题"})],
    )
    returned = submit_translation(root, batch_id, input_path)
    assert returned[0].revision == 1
    assert (root / "translations" / "history.jsonl").read_bytes() == before


def test_source_rebinding_does_not_create_translation_revision(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path)
    for manifest in manifests:
        _submit(root, manifest.batch_id)
    for manifest in manifests:
        _audit_and_approve(root, manifest.batch_id)
    assert all(audit_coverage(root, manifest.batch_id)["complete"] for manifest in manifests)

    batch_id = manifests[1].batch_id
    history_path = root / "translations" / "history.jsonl"
    history_before = history_path.read_bytes()
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    middle_unit_id = load_manifest(root, batch_id).translatable_unit_ids[0]
    middle_index = next(
        index for index, unit in enumerate(units) if unit.unit_id == middle_unit_id
    )
    new_hash = "f" * 64
    units[middle_index] = units[middle_index].model_copy(
        update={"source_hash": new_hash}
    )
    write_jsonl(units_path, units)
    current = {
        record.unit_id: record
        for record in read_jsonl(
            root / "translations" / "current.jsonl", TranslationRecord
        )
    }[middle_unit_id]
    input_path = root / "batches" / batch_id / "rebind.jsonl"
    write_jsonl(input_path, [current.model_copy(update={"source_hash": new_hash})])
    returned = submit_translation(root, batch_id, input_path)
    assert returned[0].revision == current.revision
    assert returned[0].status is ProjectStatus.REVISED
    assert returned[0].source_hash == new_hash
    assert history_path.read_bytes() == history_before
    assert load_project(root).status is ProjectStatus.REVISED
    assert all(
        not audit_coverage(root, manifest.batch_id)["complete"]
        for manifest in manifests
    )
    with pytest.raises(ValueError, match="not_publishable"):
        render_project(root, None, "stale-rebound", batch_id=batch_id)


def test_revision_invalidates_only_dependency_closure(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path)
    for manifest in manifests:
        _submit(root, manifest.batch_id)
        _audit_and_approve(root, manifest.batch_id)
        assert audit_coverage(root, manifest.batch_id)["complete"]
    _submit(root, manifests[1].batch_id, "修订")
    invalidated = {
        path.stem.removesuffix(".invalidations"): set(read_json(path)["units"])
        for path in (root / "evidence" / "audits").glob("*.invalidations.json")
    }
    assert invalidated == {
        manifests[0].batch_id: {"u001"},
        manifests[1].batch_id: {"u002"},
        manifests[2].batch_id: {"u003"},
    }
    assert all(not audit_coverage(root, manifest.batch_id)["complete"] for manifest in manifests)


def test_page_receipts_skip_unchanged_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _make_project(tmp_path, 1)
    receipt_path = root / "evidence" / "pages" / "page-0001.json"
    before = receipt_path.read_bytes()

    import littrans.verification as verification

    def fail_report(*args: object, **kwargs: object) -> str:
        raise AssertionError("cached verification attempted to render")

    monkeypatch.setattr(verification, "_write_visual_report", fail_report)
    require_verified_extraction(root, {1})
    assert receipt_path.read_bytes() == before


def test_page_receipt_does_not_hide_new_blocking_issue(tmp_path: Path) -> None:
    root, _ = _make_project(tmp_path, 1)
    issues_path = root / "derived" / "extraction-issues.jsonl"
    issue = ExtractionIssue(
        issue_id="manual-blocker",
        page=1,
        severity=Severity.BLOCKER,
        code="manual-review-blocker",
        message="A later manual review found a blocking extraction defect.",
    )
    write_jsonl(issues_path, [issue])

    blocked = verify_extraction(root, "1")
    assert not blocked["passed"]
    assert blocked["cached_pages"] == []
    assert {item["code"] for item in blocked["errors"]} == {
        "open-extraction-issue"
    }
    with pytest.raises(ValueError, match="open-extraction-issue"):
        require_verified_extraction(root, {1})

    write_jsonl(
        issues_path,
        [issue.model_copy(update={"status": IssueStatus.RESOLVED})],
    )
    refreshed = verify_extraction(root, "1")
    assert refreshed["passed"]
    assert refreshed["verified_pages"] == [1]
    assert verify_extraction(root, "1")["cached_pages"] == [1]


def test_three_batch_audit_packets_compose_unit_coverage(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path)
    for manifest in manifests:
        _submit(root, manifest.batch_id)
        assert run_qa(root, manifest.batch_id).passed
    batch_ids = [manifest.batch_id for manifest in manifests]
    legacy_bytes = sum(
        (root / "batches" / batch_id / filename).stat().st_size
        for batch_id in batch_ids
        for filename in ("source.md", "context.md")
    )
    for lens in ("fidelity", "technical", "chinese-style"):
        packet = create_workflow_packet(root, "audit", batch_ids, lens)
        assert packet.total_bytes < legacy_bytes
        issues = root / "packets" / packet.packet_id / "issues.jsonl"
        write_jsonl(issues, [])
        result = import_review_set(
            root,
            root / "packets" / packet.packet_id / "manifest.json",
            issues,
        )
        assert result["lens"] == lens
    assert all(audit_coverage(root, batch_id)["complete"] for batch_id in batch_ids)


def test_memory_is_current_approved_relevant_and_bounded(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path)
    for manifest in manifests:
        _submit(root, manifest.batch_id)
        current = translation_map(root)
        unit_id = manifest.translatable_unit_ids[0]
        current[unit_id] = current[unit_id].model_copy(
            update={"status": ProjectStatus.MACHINE_REVIEWED}
        )
        write_jsonl(root / "translations" / "current.jsonl", current.values())
    memories = translation_memory(root, manifests[1].unit_ids, limit=6)
    assert 1 <= len(memories) <= 6
    assert all(item["unit_id"] not in manifests[1].unit_ids for item in memories)
    assert memories[0]["unit_id"] in {"u001", "u003"}


def test_external_review_switches_between_incremental_and_full(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    original = load_manifest(root, manifests[0].batch_id)
    base_unit = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)[0]
    units = [
        base_unit.model_copy(
            update={
                "unit_id": f"u{index:03}",
                "bbox": (45, 40 + index * 20, 560, 58 + index * 20),
            }
        )
        for index in range(1, 6)
    ]
    write_jsonl(root / "derived" / "units.jsonl", units)
    manifest = original.model_copy(
        update={
            "unit_ids": [unit.unit_id for unit in units],
            "translatable_unit_ids": [unit.unit_id for unit in units],
        }
    )
    write_yaml(
        root / "batches" / manifest.batch_id / "manifest.yaml",
        manifest.model_dump(mode="json"),
    )
    records = [
        TranslationRecord(
            unit_id=unit.unit_id,
            target_text=f"技术译文{index}",
            source_hash=unit.source_hash,
            status=ProjectStatus.EXTERNAL_REVIEWED,
        )
        for index, unit in enumerate(units, 1)
    ]
    write_jsonl(root / "translations" / "current.jsonl", records)
    config = load_project(root)
    config.external_review = ExternalReviewConfig(
        reviewers=[
            ExternalReviewerConfig(
                id="claude",
                driver="claude-code",
                command="claude",
                model="claude-sonnet-5",
                effort="high",
                fast=False,
            )
        ]
    )
    save_project(root, config)
    snapshot = batch_unit_fingerprints(root, manifest.batch_id)
    run = ExternalReviewRun(
        run_id="base",
        batch_id=manifest.batch_id,
        reviewer_id="claude",
        driver="claude-code",
        role="primary",
        requested_model="claude-sonnet-5",
        actual_model="claude-sonnet-5",
        model_verified=True,
        translation_fingerprint="legacy",
        packet_sha256="0" * 64,
        prompt_version="test",
        verdict=ExternalReviewVerdict.ACCEPTED,
        summary="No substantive defects.",
        covered_unit_ids=list(snapshot),
        unit_fingerprints=snapshot,
        source_fingerprint=batch_source_fingerprint(root, manifest.batch_id),
        structure_fingerprint=batch_structure_fingerprint(root, manifest.batch_id),
    )
    append_jsonl(root / "reviews" / f"{manifest.batch_id}.external-runs.jsonl", [run])
    records[2] = records[2].model_copy(update={"target_text": "局部技术修订", "revision": 2})
    write_jsonl(root / "translations" / "current.jsonl", records)
    scope, base, covered, reviewer = _primary_review_scope(root, manifest.batch_id, None)
    assert scope is ReviewScope.INCREMENTAL
    assert base and base.run_id == "base"
    assert reviewer == "claude"
    assert units[2].unit_id in covered
    records[3] = records[3].model_copy(update={"target_text": "第二处技术修订", "revision": 2})
    write_jsonl(root / "translations" / "current.jsonl", records)
    assert _primary_review_scope(root, manifest.batch_id, None)[0] is ReviewScope.FULL


def test_v3_migration_preserves_translation_bytes_and_imports_evidence(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    units[0] = units[0].model_copy(update={"kind": UnitKind.CAPTION})
    write_jsonl(units_path, units)
    translation_input = root / "batches" / batch_id / "caption.jsonl"
    write_jsonl(
        translation_input,
        [
            TranslationRecord(
                unit_id=units[0].unit_id,
                target_text="图 1-1。标题",
                source_hash=units[0].source_hash,
            )
        ],
    )
    submit_translation(root, batch_id, translation_input)
    empty = root / "reviews" / "legacy-audit.jsonl"
    write_jsonl(empty, [])
    import_review(root, batch_id, empty)

    record = translation_map(root)[units[0].unit_id]
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
    source_fingerprint = sha256_text(
        units[0].model_dump_json(include=source_fields, exclude_none=True)
    )
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
    legacy_fingerprint = sha256_text(
        f"{record.unit_id}|{record.source_hash}|{source_fingerprint}|"
        f"{record.revision}|{sha256_text(translation_json)}"
    )
    assert _legacy_v3_batch_fingerprint(root, batch_id) == legacy_fingerprint

    config = load_project(root)
    config.schema_version = 3
    save_project(root, config)
    qa_path = root / "qa" / f"{batch_id}.json"
    write_json(
        qa_path,
        {
            "schema_version": 1,
            "batch_id": batch_id,
            "passed": True,
            "translation_fingerprint": legacy_fingerprint,
            "errors": [],
            "warnings": [],
        },
    )
    audit_path = root / "reviews" / f"{batch_id}.audit.json"
    audit = read_json(audit_path)
    audit["translation_fingerprint"] = legacy_fingerprint
    audit.pop("unit_coverage", None)
    audit.pop("missing_coverage", None)
    write_json(audit_path, audit)
    (root / "evidence" / "audits" / f"{batch_id}.jsonl").unlink()
    runs_path = root / "reviews" / f"{batch_id}.external-runs.jsonl"
    append_jsonl(
        runs_path,
        [
            ExternalReviewRun(
                schema_version=1,
                run_id="legacy-run",
                batch_id=batch_id,
                reviewer_id="legacy-reviewer",
                driver="claude-code",
                role="primary",
                requested_model="legacy-model",
                actual_model="legacy-model",
                model_verified=True,
                translation_fingerprint=legacy_fingerprint,
                packet_sha256="0" * 64,
                prompt_version="v3",
                verdict=ExternalReviewVerdict.ACCEPTED,
                summary="Accepted under the v3 evidence contract.",
            )
        ],
    )
    current_before = (root / "translations" / "current.jsonl").read_bytes()
    history_before = (root / "translations" / "history.jsonl").read_bytes()
    preview = migrate_project_schema(root, 4, dry_run=True)
    assert preview["changed"] is False
    assert preview["importable"] == {
        "qa": 1,
        "audit_lenses": 3,
        "external_runs": 1,
    }
    assert preview["pending_recheck"] == {"qa": [], "audit": [], "external": []}
    report = migrate_project_schema(root, 4)
    assert report["source_verification"]["passed"]
    assert load_project(root).schema_version == 4
    assert (root / "translations" / "current.jsonl").read_bytes() == current_before
    assert (root / "translations" / "history.jsonl").read_bytes() == history_before
    assert audit_coverage(root, batch_id)["complete"]
    migrated_runs = read_jsonl(runs_path, ExternalReviewRun)
    migrated = next(run for run in migrated_runs if run.run_id == "legacy-run-v4")
    assert migrated.schema_version == 2
    assert migrated.base_run_id == "legacy-run"


def test_exact_three_batch_render_runs_seam_qa(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path)
    for manifest in manifests:
        _submit(root, manifest.batch_id)
        _audit_and_approve(root, manifest.batch_id)
    batch_ids = [manifest.batch_id for manifest in manifests]
    outputs = render_project(
        root,
        None,
        "three-batch",
        batch_ids=batch_ids,
    )
    render_qa = json.loads(Path(outputs["render_qa"]).read_text(encoding="utf-8"))
    assert render_qa["passed"]
    assert render_qa["selection"]["batch_ids"] == batch_ids
    assert render_qa["unit_ids"] == ["u001", "u002", "u003"]
