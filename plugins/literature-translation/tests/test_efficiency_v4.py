from __future__ import annotations

import json
import runpy
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from littrans import external_review
from littrans.batching import create_batches, load_manifest, refresh_batch
from littrans.evidence import (
    audit_context_text,
    batch_source_fingerprint,
    batch_structure_fingerprint,
    batch_unit_fingerprints,
    dependency_closure,
    page_evidence_units,
    relevant_terms,
    translation_memory,
)
from littrans.external_review import _primary_review_scope, external_review_status
from littrans.migration import (
    _legacy_v3_batch_fingerprint,
    _migratable_v3_external_chain,
    migrate_project_schema,
)
from littrans.models import (
    AuditRun,
    ExternalReviewConfig,
    ExternalReviewDriver,
    ExternalReviewerConfig,
    ExternalReviewRun,
    ExternalReviewVerdict,
    ExtractionIssue,
    FigureLabel,
    IssueStatus,
    IssueType,
    PageVerificationReceipt,
    ProjectStatus,
    PromptDelivery,
    ReaderNote,
    RenderPolicy,
    ReviewIssue,
    ReviewScope,
    ReviewUsage,
    SemanticStatus,
    Severity,
    SidebarRole,
    SourceUnit,
    TableData,
    TranslationRecord,
    UnitKind,
    WorkflowPacketManifest,
)
from littrans.project import initialize_project, translation_map
from littrans.quality import (
    approve_batch,
    audit_coverage,
    import_review,
    qa_report_is_current,
    resolve_issue,
    run_qa,
)
from littrans.rendering import render_project
from littrans.storage import (
    append_jsonl,
    atomic_write_text,
    load_project,
    read_json,
    read_jsonl,
    save_project,
    sha256_file,
    sha256_text,
    write_json,
    write_jsonl,
    write_yaml,
)
from littrans.translation import submit_translation
from littrans.verification import require_verified_extraction, verify_extraction
from littrans.workflow import (
    create_workflow_packet,
    import_review_set,
    workflow_metrics,
    workflow_next,
)


def test_claude_stdin_delivery_remains_shadow_gated() -> None:
    assert external_review.CLAUDE_STDIN_PROMPT_DELIVERY_ENABLED is False


def test_shadow_ab_forces_distinct_delivery_arms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(str(repo_root / "scripts" / "shadow_external_ab.py"))
    variant_delivery = namespace["_variant_delivery"]

    assert variant_delivery("legacy") is PromptDelivery.FILE
    assert variant_delivery("optimized") is PromptDelivery.STDIN
    with pytest.raises(ValueError, match="Unknown shadow variant"):
        variant_delivery("unexpected")

    reviewer = ExternalReviewerConfig(
        id="shadow",
        driver="claude-code",
        command="claude",
        model="claude-sonnet-5",
        fast=False,
    )
    deliveries: list[PromptDelivery] = []

    def invoke(*args: object, **kwargs: object) -> tuple[object, ...]:
        delivery = kwargs["forced_delivery"]
        assert isinstance(delivery, PromptDelivery)
        deliveries.append(delivery)
        return (
            {"verdict": "accepted", "summary": "No defects found.", "issues": []},
            "{}",
            reviewer.model,
            reviewer.effort,
            reviewer.model,
            "off",
            1,
            delivery,
            1.0,
            ReviewUsage(input_tokens=100, provider_turns=2),
            0.01,
        )

    run_ab = namespace["run_ab"]
    globals_ = run_ab.__globals__
    monkeypatch.setitem(
        globals_,
        "load_project",
        lambda root: SimpleNamespace(
            external_review=ExternalReviewConfig(reviewers=[reviewer])
        ),
    )
    monkeypatch.setitem(globals_, "load_manifest", lambda *args: SimpleNamespace())
    monkeypatch.setitem(globals_, "_require_machine_reviewed", lambda *args: None)
    monkeypatch.setitem(globals_, "_batch_stage", lambda *args: "complete")
    monkeypatch.setitem(
        globals_,
        "_defect_snapshot",
        lambda *args: (
            {},
            [
                {
                    "issue_id": "seed-major",
                    "unit_id": "u001",
                    "severity": Severity.MAJOR.value,
                    "type": IssueType.MEANING.value,
                }
            ],
        ),
    )
    monkeypatch.setitem(
        globals_, "_packet_text", lambda *args, **kwargs: ("packet", [1])
    )
    monkeypatch.setitem(
        globals_, "_render_packet", lambda root, packet_dir, text, pages: packet_dir / "packet.md"
    )
    monkeypatch.setitem(globals_, "_evidence_map", lambda *args, **kwargs: {})
    monkeypatch.setitem(globals_, "_invoke", invoke)

    result = run_ab(tmp_path, ["batch"], {"batch"}, reviewer.id)

    assert deliveries == [PromptDelivery.FILE, PromptDelivery.STDIN]
    assert result["delivery_protocol_passed"] is True


def test_shadow_ab_validates_all_batches_before_provider_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(str(repo_root / "scripts" / "shadow_external_ab.py"))
    reviewer = ExternalReviewerConfig(
        id="shadow",
        driver="claude-code",
        command="claude",
        model="claude-sonnet-5",
        fast=False,
    )
    run_ab = namespace["run_ab"]
    globals_ = run_ab.__globals__
    batch_ids = ["clean-1", "clean-2", "clean-3", "clean-4", "clean-5", "missing"]
    validated: list[str] = []
    provider_called = False

    def load_manifest(_root: Path, batch_id: str) -> SimpleNamespace:
        validated.append(batch_id)
        if batch_id == "missing":
            raise ValueError("Unknown batch ID: missing")
        return SimpleNamespace(batch_id=batch_id)

    def forbidden_invoke(*args: object, **kwargs: object) -> tuple[object, ...]:
        nonlocal provider_called
        provider_called = True
        raise AssertionError("provider must not be called")

    monkeypatch.setitem(
        globals_,
        "load_project",
        lambda root: SimpleNamespace(
            external_review=ExternalReviewConfig(reviewers=[reviewer])
        ),
    )
    monkeypatch.setitem(globals_, "load_manifest", load_manifest)
    monkeypatch.setitem(globals_, "_invoke", forbidden_invoke)

    with pytest.raises(ValueError, match="Unknown batch ID: missing"):
        run_ab(tmp_path, batch_ids, set(), reviewer.id)

    assert validated == batch_ids
    assert not provider_called


def test_shadow_ab_validates_clean_baselines_before_provider_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(str(repo_root / "scripts" / "shadow_external_ab.py"))
    reviewer = ExternalReviewerConfig(
        id="shadow",
        driver="claude-code",
        command="claude",
        model="claude-sonnet-5",
        fast=False,
    )
    run_ab = namespace["run_ab"]
    globals_ = run_ab.__globals__
    batch_ids = ["clean-1", "clean-2", "clean-3", "clean-4", "clean-5", "draft"]
    validated: list[str] = []
    stages: list[str] = []
    provider_called = False

    def require_clean(_root: Path, batch_id: str) -> None:
        validated.append(batch_id)

    def batch_stage(_root: Path, batch_id: str) -> str:
        stages.append(batch_id)
        return "external-review" if batch_id == "draft" else "complete"

    def forbidden_invoke(*args: object, **kwargs: object) -> tuple[object, ...]:
        nonlocal provider_called
        provider_called = True
        raise AssertionError("provider must not be called")

    monkeypatch.setitem(
        globals_,
        "load_project",
        lambda root: SimpleNamespace(
            external_review=ExternalReviewConfig(reviewers=[reviewer])
        ),
    )
    monkeypatch.setitem(globals_, "load_manifest", lambda *args: SimpleNamespace())
    monkeypatch.setitem(globals_, "_require_machine_reviewed", require_clean)
    monkeypatch.setitem(globals_, "_batch_stage", batch_stage)
    monkeypatch.setitem(globals_, "_invoke", forbidden_invoke)

    with pytest.raises(ValueError, match="draft is at workflow stage external-review"):
        run_ab(tmp_path, batch_ids, set(), reviewer.id)

    assert validated == batch_ids
    assert stages == batch_ids
    assert not provider_called


def test_shadow_ab_rejects_vacuous_severe_recall_before_provider_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(str(repo_root / "scripts" / "shadow_external_ab.py"))
    reviewer = ExternalReviewerConfig(
        id="shadow",
        driver="claude-code",
        command="claude",
        model="claude-sonnet-5",
        fast=False,
    )
    run_ab = namespace["run_ab"]
    globals_ = run_ab.__globals__
    batch_ids = [f"batch-{index}" for index in range(6)]
    defect_ids = set(batch_ids[:3])
    provider_called = False

    def minor_snapshot(
        _root: Path, batch_id: str
    ) -> tuple[dict[str, TranslationRecord], list[dict[str, str]]]:
        return {}, [
            {
                "issue_id": f"{batch_id}-minor",
                "unit_id": "u001",
                "severity": Severity.MINOR.value,
                "type": IssueType.MEANING.value,
            }
        ]

    def forbidden_invoke(*args: object, **kwargs: object) -> tuple[object, ...]:
        nonlocal provider_called
        provider_called = True
        raise AssertionError("provider must not be called")

    monkeypatch.setitem(
        globals_,
        "load_project",
        lambda root: SimpleNamespace(
            external_review=ExternalReviewConfig(reviewers=[reviewer])
        ),
    )
    monkeypatch.setitem(globals_, "load_manifest", lambda *args: SimpleNamespace())
    monkeypatch.setitem(globals_, "_require_machine_reviewed", lambda *args: None)
    monkeypatch.setitem(globals_, "_batch_stage", lambda *args: "complete")
    monkeypatch.setitem(globals_, "_defect_snapshot", minor_snapshot)
    monkeypatch.setitem(globals_, "_invoke", forbidden_invoke)

    with pytest.raises(ValueError, match="blocker/major gold defect"):
        run_ab(tmp_path, batch_ids, defect_ids, reviewer.id)

    assert not provider_called


def test_shadow_defect_snapshot_requires_unambiguous_history_match(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(str(repo_root / "scripts" / "shadow_external_ab.py"))
    defect_snapshot = namespace["_defect_snapshot"]
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _submit(root, batch_id, suffix="第一次修订")
    _submit(root, batch_id, suffix="第二次修订")
    unit_id = manifests[0].translatable_unit_ids[0]
    issues_path = root / "reviews" / f"{batch_id}.issues.jsonl"
    issue = ReviewIssue(
        issue_id="ambiguous-history",
        batch_id=batch_id,
        unit_id=unit_id,
        severity=Severity.MAJOR,
        type=IssueType.MEANING,
        explanation="A resolved historical defect.",
        reviewer="historical-auditor",
        status=IssueStatus.RESOLVED,
        resolution="Corrected in the current translation.",
        resolved_at="2026-01-01T00:00:00+00:00",
    )

    write_jsonl(issues_path, [issue])
    with pytest.raises(ValueError, match="No reconstructable historical defect"):
        defect_snapshot(root, batch_id)

    write_jsonl(
        issues_path,
        [issue.model_copy(update={"target_span": "不存在的历史片段"})],
    )
    with pytest.raises(ValueError, match="No reconstructable historical defect"):
        defect_snapshot(root, batch_id)

    write_jsonl(
        issues_path,
        [issue.model_copy(update={"target_span": "第一次修订"})],
    )
    overrides, gold = defect_snapshot(root, batch_id)
    assert overrides[unit_id].revision == 2
    assert gold == [
        {
            "issue_id": issue.issue_id,
            "unit_id": unit_id,
            "severity": Severity.MAJOR.value,
            "type": IssueType.MEANING.value,
        }
    ]

    for rejected_status in (IssueStatus.REJECTED, IssueStatus.WAIVED):
        write_jsonl(
            issues_path,
            [issue.model_copy(update={"status": rejected_status})],
        )
        with pytest.raises(ValueError, match="No reconstructable historical defect"):
            defect_snapshot(root, batch_id)


def test_shadow_recall_requires_batch_and_severity_match() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(str(repo_root / "scripts" / "shadow_external_ab.py"))
    recall = namespace["_recall"]
    gold = {
        "unit_id": "shared-unit",
        "type": IssueType.MEANING.value,
        "severity": Severity.MAJOR.value,
    }
    downgraded = {
        "unit_id": gold["unit_id"],
        "type": gold["type"],
        "severity": Severity.SUGGESTION.value,
    }
    exact = {**downgraded, "severity": Severity.MAJOR.value}

    assert recall(
        [
            {
                "batch_id": "gold-batch",
                "gold": [gold],
                "issues": [downgraded],
            },
            {
                "batch_id": "different-batch",
                "gold": [],
                "issues": [exact],
            },
        ],
        {Severity.BLOCKER.value, Severity.MAJOR.value},
    ) == 0.0
    assert recall(
        [{"batch_id": "gold-batch", "gold": [gold], "issues": [exact]}],
        {Severity.BLOCKER.value, Severity.MAJOR.value},
    ) == 1.0


def test_completed_benchmark_uses_effective_workflow_gates(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(str(repo_root / "scripts" / "benchmark_efficiency.py"))
    benchmark = namespace["benchmark"]
    root, manifests = _make_project(tmp_path, pages=2, max_words=100)
    complete, incomplete = manifests
    _submit(root, complete.batch_id)
    _submit(root, complete.batch_id, suffix="修订")
    _submit(root, incomplete.batch_id)
    _audit_and_approve(root, complete.batch_id)
    failed = ExternalReviewRun(
        run_id="failed-attempt",
        batch_id=incomplete.batch_id,
        reviewer_id="claude",
        driver=ExternalReviewDriver.CLAUDE_CODE,
        role="primary",
        requested_model="claude-sonnet-5",
        model_verified=False,
        translation_fingerprint=external_review.batch_translation_fingerprint(
            root, incomplete.batch_id
        ),
        packet_sha256="0" * 64,
        prompt_version="test",
        verdict=ExternalReviewVerdict.INCONCLUSIVE,
        summary="The reviewer process failed.",
        success=False,
    )
    append_jsonl(
        root / "reviews" / f"{incomplete.batch_id}.external-runs.jsonl",
        [failed],
    )

    result = benchmark(root, completed_only=True)
    completed_units = set(complete.unit_ids)
    expected_history = [
        record
        for record in read_jsonl(
            root / "translations" / "history.jsonl", TranslationRecord
        )
        if record.unit_id in completed_units
    ]

    assert result["batches"] == 1
    assert result["history_records"] == len(expected_history) == 2


def test_completed_benchmark_rejects_an_empty_population(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(str(repo_root / "scripts" / "benchmark_efficiency.py"))
    benchmark = namespace["benchmark"]
    root, _ = _make_project(tmp_path, pages=1)

    with pytest.raises(ValueError, match="at least one selected batch"):
        benchmark(root, completed_only=True)


def test_benchmark_ignores_history_for_removed_source_units(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(str(repo_root / "scripts" / "benchmark_efficiency.py"))
    benchmark = namespace["benchmark"]
    root, _ = _make_project(tmp_path, pages=1)
    removed = TranslationRecord(
        unit_id="u999",
        target_text="已删除单元的旧译文",
        source_hash="removed-source",
    )
    write_jsonl(
        root / "translations" / "history.jsonl",
        [
            removed,
            removed.model_copy(update={"target_text": "历史修订", "revision": 2}),
        ],
    )

    result = benchmark(root, completed_only=False)

    assert result["history_records"] == 0
    assert result["semantic_noop_records"] == 0
    assert result["semantic_change_records"] == 0


def test_completed_benchmark_does_not_group_across_incomplete_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(str(repo_root / "scripts" / "benchmark_efficiency.py"))
    benchmark = namespace["benchmark"]
    root, manifests = _make_project(tmp_path, pages=3, max_words=100)
    first, middle, last = manifests
    for manifest in manifests:
        _submit(root, manifest.batch_id)
    _audit_and_approve(root, first.batch_id)
    _audit_and_approve(root, last.batch_id)
    original_shared_context = benchmark.__globals__["_shared_context"]
    original_read_only_context = benchmark.__globals__["_audit_read_only_context"]
    shared_groups: list[list[str]] = []

    def capture_shared_context(project_root: Path, units: list[SourceUnit]) -> str:
        shared_groups.append([unit.unit_id for unit in units])
        return original_shared_context(project_root, units)

    monkeypatch.setitem(
        benchmark.__globals__, "_shared_context", capture_shared_context
    )

    result = benchmark(root, completed_only=True)
    base_shared_groups = list(shared_groups)
    marker = "x" * 101

    def enlarge_read_only_context(
        units: list[SourceUnit], translations: dict[str, TranslationRecord]
    ) -> str:
        return original_read_only_context(units, translations) + marker

    monkeypatch.setitem(
        benchmark.__globals__, "_audit_read_only_context", enlarge_read_only_context
    )
    enlarged = benchmark(root, completed_only=True)

    assert result["batches"] == 2
    assert base_shared_groups[::2] == [list(first.unit_ids), list(last.unit_ids)]
    assert all(
        not (set(first.unit_ids) & set(group) and set(last.unit_ids) & set(group))
        for group in base_shared_groups
    )
    assert enlarged["optimized_packet_bytes"] - result["optimized_packet_bytes"] == (
        len(marker.encode("utf-8")) * 3 * 2
    )


def test_benchmark_splits_overlapping_manifests_into_executable_groups(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(str(repo_root / "scripts" / "benchmark_efficiency.py"))
    root, manifests = _make_project(tmp_path, pages=1)
    duplicate = create_batches(
        root, "1", max_words=700, prefix="benchmark-overlap"
    )[0]
    selected_ids = {manifests[0].batch_id, duplicate.batch_id}
    ordered = [
        manifest
        for manifest in namespace["_all_manifests"](root)
        if manifest.batch_id in selected_ids
    ]

    groups = namespace["_consecutive_packet_groups"](ordered, selected_ids)

    assert [manifest.batch_id for group in groups for manifest in group] == [
        manifest.batch_id for manifest in ordered
    ]
    assert len(groups) == 2
    for group in groups:
        batch_ids = [manifest.batch_id for manifest in group]
        packet = create_workflow_packet(root, "translate", batch_ids)
        assert packet.batch_ids == batch_ids


def test_shadow_cli_rejects_duplicate_batch_samples_before_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(str(repo_root / "scripts" / "shadow_external_ab.py"))
    main = namespace["main"]
    called = False

    def forbidden_run(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setitem(main.__globals__, "run_ab", forbidden_run)
    monkeypatch.setattr(
        main.__globals__["sys"],
        "argv",
        [
            "shadow_external_ab.py",
            str(tmp_path),
            "--batch-ids",
            "b1,b2,b3,b4,b5,b5",
            "--defect-batch-ids",
            "b1,b2,b3",
            "--reviewer",
            "claude",
            "--output",
            str(tmp_path / "result.json"),
        ],
    )

    with pytest.raises(ValueError, match="six distinct batch IDs"):
        main()
    assert not called


def test_workflow_metrics_rejects_unknown_batch_ids(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, pages=2, max_words=100)

    with pytest.raises(ValueError, match=r"Unknown batch IDs: \['stale-batch'\]"):
        workflow_metrics(root, [manifests[0].batch_id, "stale-batch"])

    packet = create_workflow_packet(
        root,
        "translate",
        [manifest.batch_id for manifest in manifests],
    )
    metrics = workflow_metrics(root, [manifests[0].batch_id])
    quotient, remainder = divmod(packet.total_bytes, len(packet.batch_ids))
    assert metrics["batch_ids"] == [manifests[0].batch_id]
    assert metrics["page_receipts"] == 1
    assert metrics["generated_packet_bytes"] == quotient + (remainder > 0)
    assert (
        metrics["generated_packet_allocation"]
        == "equal-per-batch-leading-remainder"
    )


def test_workflow_metrics_ignores_history_for_removed_source_units(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _submit(root, batch_id, suffix="修订")
    write_jsonl(root / "derived" / "units.jsonl", [])

    metrics = workflow_metrics(root, [batch_id])

    assert metrics["history_records"] == 0
    assert metrics["semantic_noop_records"] == 0


def _make_project(
    tmp_path: Path, pages: int = 3, max_words: int = 100
) -> tuple[Path, list[object]]:
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
    manifests = create_batches(root, "all", max_words=max_words, prefix="v4")
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


def _store_manual_audit_packet(
    root: Path, packet: WorkflowPacketManifest
) -> tuple[WorkflowPacketManifest, Path]:
    assert packet.stage == "audit"
    packet_dir = root / "packets" / packet.packet_id
    packet_dir.mkdir(parents=True, exist_ok=True)
    file_paths = {"shared": packet_dir / "shared.md"}
    file_paths.update(
        {
            f"{batch_id}:audit": packet_dir / f"{batch_id}.audit.md"
            for batch_id in packet.batch_ids
        }
    )
    units = {
        unit.unit_id: unit
        for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    }
    for file_id, path in file_paths.items():
        content = (
            audit_context_text(
                root,
                [
                    units[unit_id]
                    for unit_id in packet.unit_fingerprints
                    if unit_id in units
                ],
            )
            if file_id == "shared"
            else f"# Reviewed packet file: {file_id}\n"
        )
        atomic_write_text(path, content)
    files = {
        file_id: str(path.relative_to(root)).replace("\\", "/")
        for file_id, path in file_paths.items()
    }
    stored = packet.model_copy(
        update={
            "files": files,
            "file_sha256": {
                file_id: sha256_file(path) for file_id, path in file_paths.items()
            },
            "total_bytes": sum(path.stat().st_size for path in file_paths.values()),
        }
    )
    manifest_path = packet_dir / "manifest.json"
    write_json(manifest_path, stored.model_dump(mode="json"))
    return stored, manifest_path


def test_failed_external_review_records_actual_prompt_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _audit_and_approve(root, batch_id)
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

    dry_run = external_review.run_external_review(root, batch_id, dry_run=True)
    assert dry_run["prompt_delivery"] == PromptDelivery.FILE

    def fail_invoke(*args: object, **kwargs: object) -> None:
        raise external_review.ExternalInvocationError(
            "External reviewer failed",
            1,
            "partial output",
            PromptDelivery.FILE,
            duration_seconds=12.5,
        )

    monkeypatch.setattr(external_review, "_invoke", fail_invoke)
    monkeypatch.setattr(external_review, "_command_version", lambda command: "test")
    status = external_review.run_external_review(root, batch_id)
    runs = read_jsonl(
        root / "reviews" / f"{batch_id}.external-runs.jsonl", ExternalReviewRun
    )
    assert not status["external_approvable"]
    assert runs[-1].prompt_delivery is PromptDelivery.FILE
    assert runs[-1].duration_seconds == 12.5
    assert not runs[-1].success


def test_external_review_uses_a_per_run_import_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifests = _make_project(tmp_path, pages=2, max_words=100)
    config = load_project(root)
    reviewer = ExternalReviewerConfig(
        id="claude",
        driver="claude-code",
        command="claude",
        model="claude-sonnet-5",
        effort="high",
        fast=False,
    )
    config.external_review = ExternalReviewConfig(reviewers=[reviewer])
    save_project(root, config)
    for manifest in manifests:
        _submit(root, manifest.batch_id)
    for manifest in manifests:
        _audit_and_approve(root, manifest.batch_id)

    def invoke(*args: object, **kwargs: object) -> tuple[object, ...]:
        return (
            {"verdict": "accepted", "summary": "No defects found.", "issues": []},
            "{}",
            reviewer.model,
            reviewer.effort,
            reviewer.model,
            "off",
            1,
            PromptDelivery.FILE,
            1.0,
            ReviewUsage(input_tokens=100, provider_turns=2),
            0.01,
        )

    import_paths: list[Path] = []

    def capture_import(
        project_root: Path,
        batch_id: str,
        input_path: Path,
        *args: object,
        **kwargs: object,
    ) -> list[ReviewIssue]:
        assert project_root == root
        assert input_path.exists()
        assert read_jsonl(input_path, ReviewIssue) == []
        import_paths.append(input_path)
        return []

    monkeypatch.setattr(external_review, "_invoke", invoke)
    monkeypatch.setattr(external_review, "_command_version", lambda command: "test")
    monkeypatch.setattr(external_review, "import_review", capture_import)

    for manifest in manifests:
        status = external_review.run_external_review(root, manifest.batch_id)
        assert status["external_approvable"]

    assert len(import_paths) == len(manifests) == 2
    assert len(set(import_paths)) == len(import_paths)
    assert all(path.name.startswith(".external-import-") for path in import_paths)
    assert all(not path.exists() for path in import_paths)


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
    input_path = root / "batches" / batch_id / "translation.jsonl"
    write_jsonl(input_path, noop)
    assert read_jsonl(input_path, TranslationRecord) == noop
    returned = submit_translation(root, batch_id, input_path)
    assert returned[0].revision == records[0].revision
    assert returned[0].status is ProjectStatus.MACHINE_REVIEWED
    assert read_jsonl(input_path, TranslationRecord) == records
    assert before == {
        "current": current_path.read_bytes(),
        "history": history_path.read_bytes(),
        "project": (root / "project.yaml").read_bytes(),
        "audit": (root / "reviews" / f"{batch_id}.audit.json").read_bytes(),
        "runs": (root / "evidence" / "audits" / f"{batch_id}.jsonl").read_bytes(),
    }


def test_explicit_fallback_figure_labels_are_semantic_noop(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    manifest = manifests[0]
    units_path = root / "derived" / "units.jsonl"
    unit = read_jsonl(units_path, SourceUnit)[0].model_copy(
        update={
            "kind": UnitKind.FIGURE,
            "figure_labels": [
                FigureLabel(source="Open", target="打开"),
                FigureLabel(source="Close", target="关闭"),
            ],
            "visual_text_status": SemanticStatus.VERIFIED,
        }
    )
    write_jsonl(units_path, [unit])
    assert verify_extraction(root, "all", force=True)["passed"]
    refresh_batch(root, manifest.batch_id)
    _submit(root, manifest.batch_id)
    _audit_and_approve(root, manifest.batch_id)

    current_path = root / "translations" / "current.jsonl"
    history_path = root / "translations" / "history.jsonl"
    batch_path = root / "batches" / manifest.batch_id / "translation.jsonl"
    prior = translation_map(root)[unit.unit_id]
    assert prior.figure_labels == []
    before = {
        "current": current_path.read_bytes(),
        "history": history_path.read_bytes(),
        "project": (root / "project.yaml").read_bytes(),
        "qa": (root / "qa" / f"{manifest.batch_id}.json").read_bytes(),
        "audit": (
            root / "reviews" / f"{manifest.batch_id}.audit.json"
        ).read_bytes(),
        "runs": (
            root / "evidence" / "audits" / f"{manifest.batch_id}.jsonl"
        ).read_bytes(),
    }
    write_jsonl(
        batch_path,
        [
            prior.model_copy(
                update={
                    "figure_labels": unit.figure_labels,
                    "revision": prior.revision + 1,
                    "status": ProjectStatus.DRAFT,
                }
            )
        ],
    )

    returned = submit_translation(root, manifest.batch_id, batch_path)

    assert returned == [prior]
    assert read_jsonl(batch_path, TranslationRecord) == [prior]
    assert audit_coverage(root, manifest.batch_id)["complete"]
    assert before == {
        "current": current_path.read_bytes(),
        "history": history_path.read_bytes(),
        "project": (root / "project.yaml").read_bytes(),
        "qa": (root / "qa" / f"{manifest.batch_id}.json").read_bytes(),
        "audit": (
            root / "reviews" / f"{manifest.batch_id}.audit.json"
        ).read_bytes(),
        "runs": (
            root / "evidence" / "audits" / f"{manifest.batch_id}.jsonl"
        ).read_bytes(),
    }


def test_new_blocking_audit_reopens_approved_batch(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _audit_and_approve(root, batch_id)
    assert (
        approve_batch(root, batch_id, "human", confirm_user_approved=True)
        is ProjectStatus.HUMAN_APPROVED
    )
    assert workflow_next(root)["stage"] == "complete"
    issue = ReviewIssue(
        issue_id="late-major",
        batch_id=batch_id,
        unit_id=load_manifest(root, batch_id).translatable_unit_ids[0],
        severity=Severity.MAJOR,
        type=IssueType.MEANING,
        explanation="A later independent audit found a substantive defect.",
        reviewer="late-independent-auditor",
    )
    issue_path = root / "reviews" / "late-major.jsonl"
    write_jsonl(issue_path, [issue])

    import_review(root, batch_id, issue_path)

    assert {
        translation_map(root)[unit_id].status
        for unit_id in load_manifest(root, batch_id).translatable_unit_ids
    } == {ProjectStatus.REVIEWED}
    assert load_project(root).status is ProjectStatus.REVIEWED
    assert workflow_next(root)["stage"] == "revise"
    with pytest.raises(ValueError, match="Open blocker/major issues remain"):
        approve_batch(root, batch_id, "machine")
    with pytest.raises(ValueError, match="open_severe=.*late-major"):
        render_project(root, None, "late-major", batch_id=batch_id)


def test_glossary_change_invalidates_qa_workflow_approval_and_render(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _audit_and_approve(root, batch_id)
    assert workflow_next(root)["stage"] == "complete"

    write_yaml(
        root / "glossary" / "approved.yaml",
        {
            "terms": [
                {
                    "source": "architecture",
                    "target": "架构",
                    "forbidden": ["体系结构"],
                }
            ]
        },
    )

    assert workflow_next(root)["stage"] == "qa"
    with pytest.raises(ValueError, match="stale for the current approved terminology"):
        approve_batch(root, batch_id, "machine")
    with pytest.raises(ValueError, match="stale_qa"):
        render_project(root, None, "stale-glossary", batch_id=batch_id)
    report = run_qa(root, batch_id)
    assert not report.passed
    assert {item.code for item in report.errors} == {"approved-term-missing"}


@pytest.mark.parametrize(
    ("relative_path", "expected_stage"),
    [
        ("context/document-brief.md", "audit"),
        ("context/style-guide.md", "audit"),
        ("glossary/approved.yaml", "qa"),
    ],
)
def test_audit_context_changes_invalidate_lens_coverage(
    tmp_path: Path, relative_path: str, expected_stage: str
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _audit_and_approve(root, batch_id)
    assert audit_coverage(root, batch_id)["complete"]
    context_path = root / relative_path
    if context_path.suffix == ".yaml":
        write_yaml(
            context_path,
            {
                "terms": [
                    {
                        "source": "architecture",
                        "target": "架构",
                        "scope": "document",
                    }
                ]
            },
        )
    else:
        atomic_write_text(
            context_path,
            context_path.read_text(encoding="utf-8").rstrip()
            + "\n\nNew mandatory audit instruction.\n",
        )

    assert not audit_coverage(root, batch_id)["complete"]
    assert workflow_next(root)["stage"] == expected_stage
    with pytest.raises(ValueError, match=f"incomplete_audit=.*{batch_id}"):
        render_project(root, None, "stale-audit-context", batch_id=batch_id)


def test_structured_source_representations_select_and_enforce_terms(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=3, max_words=700)
    assert len(manifests) == 1
    manifest = manifests[0]
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    for index, unit in enumerate(units):
        plain_source = unit.source_text.replace("architecture", "system")
        units[index] = unit.model_copy(
            update={
                "source_text": plain_source,
                "source_hash": sha256_text(plain_source),
            }
        )
    units[0] = units[0].model_copy(update={"source_markdown": "Architecture"})
    units[1] = units[1].model_copy(
        update={
            "kind": UnitKind.TABLE,
            "table": TableData(
                rows=[["Architecture", "Value"]],
                header_rows=1,
                column_count=2,
            ),
        }
    )
    units[2] = units[2].model_copy(
        update={
            "kind": UnitKind.FIGURE,
            "figure_labels": [
                FigureLabel(source="Architecture", target="旧标签")
            ],
            "visual_text_status": SemanticStatus.VERIFIED,
        }
    )
    write_jsonl(units_path, units)
    assert all("architecture" not in unit.source_text.casefold() for unit in units)
    assert verify_extraction(root, "all", force=True)["passed"]
    write_yaml(
        root / "glossary" / "approved.yaml",
        {
            "terms": [
                {
                    "source": "architecture",
                    "target": "架构",
                    "forbidden": ["体系结构"],
                }
            ]
        },
    )
    selected_terms = relevant_terms(root, units)
    assert [term["source"] for term in selected_terms] == ["architecture"]
    records = [
        TranslationRecord(
            unit_id=units[0].unit_id,
            target_text="结构说明",
            source_hash=units[0].source_hash,
        ),
        TranslationRecord(
            unit_id=units[1].unit_id,
            target_text="结构化表格",
            target_table=TableData(
                rows=[["结构", "值"]],
                header_rows=1,
                column_count=2,
            ),
            source_hash=units[1].source_hash,
        ),
        TranslationRecord(
            unit_id=units[2].unit_id,
            target_text="结构图",
            figure_labels=[
                FigureLabel(source="Architecture", target="体系结构")
            ],
            source_hash=units[2].source_hash,
        ),
    ]
    input_path = root / "batches" / manifest.batch_id / "structured-terms.jsonl"
    write_jsonl(input_path, records)
    submit_translation(root, manifest.batch_id, input_path)

    report = run_qa(root, manifest.batch_id)
    missing_units = {
        item.unit_id for item in report.errors if item.code == "approved-term-missing"
    }
    forbidden_units = {
        item.unit_id for item in report.errors if item.code == "forbidden-term"
    }
    assert missing_units == set(manifest.unit_ids)
    assert forbidden_units == {units[2].unit_id}


def test_qa_uses_rendered_source_figure_label_fallback(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    manifest = manifests[0]
    units_path = root / "derived" / "units.jsonl"
    original = read_jsonl(units_path, SourceUnit)[0]
    source = original.source_text.replace("architecture", "system")
    unit = original.model_copy(
        update={
            "kind": UnitKind.FIGURE,
            "source_text": source,
            "source_hash": sha256_text(source),
            "figure_labels": [
                FigureLabel(source="Architecture", target="架构")
            ],
            "visual_text_status": SemanticStatus.VERIFIED,
        }
    )
    write_jsonl(units_path, [unit])
    assert verify_extraction(root, "all", force=True)["passed"]
    refresh_batch(root, manifest.batch_id)
    write_yaml(
        root / "glossary" / "approved.yaml",
        {
            "terms": [
                {
                    "source": "architecture",
                    "target": "架构",
                    "scope": "document",
                }
            ]
        },
    )
    input_path = root / "batches" / manifest.batch_id / "figure-fallback.jsonl"
    write_jsonl(
        input_path,
        [
            TranslationRecord(
                unit_id=unit.unit_id,
                target_text="该图展示控件。",
                source_hash=unit.source_hash,
            )
        ],
    )
    submit_translation(root, manifest.batch_id, input_path)

    report = run_qa(root, manifest.batch_id)
    assert report.passed, report.errors
    assert not {
        item.code
        for item in report.errors
        if item.code in {"approved-term-missing", "forbidden-term"}
    }
    packet_text, _ = external_review._packet_text(root, manifest.batch_id)
    assert "Figure label sources:\n- Architecture" in packet_text
    assert "Figure label translations:\n- 架构" in packet_text
    evidence_source, evidence_target = external_review._evidence_map(
        root, manifest.batch_id
    )[unit.unit_id]
    assert "Figure label sources:\n- Architecture" in evidence_source
    assert "Figure label translations:\n- 架构" in evidence_target


def test_qa_checks_numbers_and_units_in_overridden_figure_labels(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    manifest = manifests[0]
    units_path = root / "derived" / "units.jsonl"
    original = read_jsonl(units_path, SourceUnit)[0]
    unit = original.model_copy(
        update={
            "kind": UnitKind.FIGURE,
            "figure_labels": [
                FigureLabel(source="Speed 10 m/s", target="速度 10 m/s")
            ],
            "visual_text_status": SemanticStatus.VERIFIED,
        }
    )
    write_jsonl(units_path, [unit])
    assert verify_extraction(root, "all", force=True)["passed"]
    refresh_batch(root, manifest.batch_id)
    input_path = root / "batches" / manifest.batch_id / "numeric-label.jsonl"
    write_jsonl(
        input_path,
        [
            TranslationRecord(
                unit_id=unit.unit_id,
                target_text="该图展示速度。",
                figure_labels=[
                    FigureLabel(source="Speed 10 m/s", target="速度 100 m/s")
                ],
                source_hash=unit.source_hash,
            )
        ],
    )
    submit_translation(root, manifest.batch_id, input_path)

    report = run_qa(root, manifest.batch_id)

    codes = {item.code for item in report.errors}
    assert "number-mismatch" in codes
    assert "number-unit-mismatch" in codes


def test_qa_deduplicates_figure_labels_already_in_source_text(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    manifest = manifests[0]
    units_path = root / "derived" / "units.jsonl"
    original = read_jsonl(units_path, SourceUnit)[0]
    label_source = "Speed 10 m/s"
    source = f"{original.source_text}\n{label_source}"
    unit = original.model_copy(
        update={
            "kind": UnitKind.FIGURE,
            "source_text": source,
            "source_hash": sha256_text(source),
            "figure_labels": [
                FigureLabel(source=label_source, target="速度 10 m/s")
            ],
            "visual_text_status": SemanticStatus.VERIFIED,
        }
    )
    write_jsonl(units_path, [unit])
    verification = verify_extraction(root, "all", force=True)
    assert verification["passed"], verification["errors"]
    refresh_batch(root, manifest.batch_id)
    input_path = root / "batches" / manifest.batch_id / "numeric-label.jsonl"
    write_jsonl(
        input_path,
        [
            TranslationRecord(
                unit_id=unit.unit_id,
                target_text="该图展示速度。",
                figure_labels=[
                    FigureLabel(source=label_source, target="速度 10 m/s")
                ],
                source_hash=unit.source_hash,
            )
        ],
    )
    submit_translation(root, manifest.batch_id, input_path)

    report = run_qa(root, manifest.batch_id)

    assert report.passed, report.errors
    assert not {
        item.code
        for item in report.errors
        if item.code in {"number-mismatch", "number-unit-mismatch"}
    }


def test_figure_label_overrides_require_complete_source_mapping(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    manifest = manifests[0]
    units_path = root / "derived" / "units.jsonl"
    original = read_jsonl(units_path, SourceUnit)[0]
    unit = original.model_copy(
        update={
            "kind": UnitKind.FIGURE,
            "figure_labels": [
                FigureLabel(source="Open", target="打开"),
                FigureLabel(source="Close", target="关闭"),
            ],
            "visual_text_status": SemanticStatus.VERIFIED,
        }
    )
    write_jsonl(units_path, [unit])
    assert verify_extraction(root, "all", force=True)["passed"]
    refresh_batch(root, manifest.batch_id)
    partial = TranslationRecord(
        unit_id=unit.unit_id,
        target_text="控件状态图。",
        figure_labels=[FigureLabel(source="Open", target="打开")],
        source_hash=unit.source_hash,
    )
    input_path = root / "batches" / manifest.batch_id / "partial-labels.jsonl"
    write_jsonl(input_path, [partial])

    with pytest.raises(ValueError, match="Figure label mapping mismatch"):
        submit_translation(root, manifest.batch_id, input_path)

    write_jsonl(root / "translations" / "current.jsonl", [partial])
    report = run_qa(root, manifest.batch_id)
    assert not report.passed
    assert [item.code for item in report.errors].count(
        "figure-label-mapping-mismatch"
    ) == 1
    with pytest.raises(ValueError, match="Figure label mapping mismatch"):
        render_project(root, "1", "partial-labels", allow_draft=True)


def test_source_only_change_requires_current_audit_before_formal_render(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=2, max_words=700)
    assert len(manifests) == 1
    batch_id = manifests[0].batch_id
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    units[1] = units[1].model_copy(
        update={
            "kind": UnitKind.CODE,
            "translatable": False,
            "code_language": "python",
        }
    )
    write_jsonl(units_path, units)
    refreshed = refresh_batch(root, batch_id)
    assert units[1].unit_id not in refreshed.translatable_unit_ids
    assert verify_extraction(root, "all", force=True)["passed"]
    _submit(root, batch_id)
    _audit_and_approve(root, batch_id)
    assert render_project(root, None, "before-source-only-change", batch_id=batch_id)

    units[1] = units[1].model_copy(update={"code_language": "javascript"})
    write_jsonl(units_path, units)
    assert verify_extraction(root, "2")["passed"]
    assert run_qa(root, batch_id).passed
    assert not audit_coverage(root, batch_id)["complete"]
    assert workflow_next(root)["stage"] == "audit"

    with pytest.raises(ValueError, match=f"incomplete_audit=.*{batch_id}"):
        render_project(root, None, "stale-source-only-audit", batch_id=batch_id)
    assert render_project(
        root,
        None,
        "draft-source-only-audit",
        allow_draft=True,
        batch_id=batch_id,
    )


def test_batch_refresh_invalidates_neighbors_of_removed_unit(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=3, max_words=700)
    assert len(manifests) == 1
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _audit_and_approve(root, batch_id)
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    removed_id = units[1].unit_id
    units[1] = units[1].model_copy(
        update={
            "render_policy": RenderPolicy.OMIT,
            "translatable": False,
        }
    )
    write_jsonl(units_path, units)
    assert verify_extraction(root, "all", force=True)["passed"]

    refreshed = refresh_batch(root, batch_id)
    assert removed_id not in refreshed.unit_ids
    assert run_qa(root, batch_id).passed
    coverage = audit_coverage(root, batch_id)
    assert not coverage["complete"]
    assert all(
        set(missing) == set(refreshed.unit_ids)
        for missing in coverage["missing"].values()
    )
    with pytest.raises(ValueError, match=f"incomplete_audit=.*{batch_id}"):
        render_project(root, None, "removed-unit", batch_id=batch_id)


def test_formal_render_requires_current_external_review_ledger(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _audit_and_approve(root, batch_id)
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
    config.status = ProjectStatus.EXTERNAL_REVIEWED
    save_project(root, config)
    current_path = root / "translations" / "current.jsonl"
    current = [
        record.model_copy(update={"status": ProjectStatus.EXTERNAL_REVIEWED})
        for record in read_jsonl(current_path, TranslationRecord)
    ]
    write_jsonl(current_path, current)

    assert workflow_next(root)["stage"] == "external-review"
    with pytest.raises(ValueError, match=f"stale_external=.*{batch_id}"):
        render_project(root, None, "stale-external", batch_id=batch_id)
    assert render_project(
        root,
        None,
        "draft-stale-external",
        allow_draft=True,
        batch_id=batch_id,
    )


def test_internal_minor_requires_revision_before_external_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _audit_and_approve(root, batch_id)
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
    minor = ReviewIssue(
        issue_id="internal-minor-before-external",
        batch_id=batch_id,
        unit_id=manifests[0].translatable_unit_ids[0],
        severity=Severity.MINOR,
        type=IssueType.MEANING,
        explanation="Resolve this substantive internal finding before paid review.",
        reviewer="independent-fidelity-auditor",
    )
    issue_path = root / "reviews" / "internal-minor.jsonl"
    write_jsonl(issue_path, [minor])
    import_review(root, batch_id, issue_path)
    invoked = False

    def forbidden_invoke(*args: object, **kwargs: object) -> tuple[object, ...]:
        nonlocal invoked
        invoked = True
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(external_review, "_invoke", forbidden_invoke)

    assert workflow_next(root)["stage"] == "revise"
    with pytest.raises(
        ValueError,
        match="unresolved substantive issues.*internal-minor-before-external",
    ):
        external_review.run_external_review(root, batch_id)
    assert not invoked


def test_external_finding_requires_resolution_before_another_paid_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _audit_and_approve(root, batch_id)
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
    issue = ReviewIssue(
        issue_id="external-major-before-rerun",
        batch_id=batch_id,
        unit_id=manifests[0].translatable_unit_ids[0],
        severity=Severity.MAJOR,
        type=IssueType.MEANING,
        explanation="The external reviewer requested a substantive revision.",
        reviewer="external:claude:claude-sonnet-5",
    )
    issue_path = root / "reviews" / "external-major.jsonl"
    write_jsonl(issue_path, [issue])
    import_review(
        root,
        batch_id,
        issue_path,
        lenses=["external:claude"],
        preserve_status=True,
    )
    invoked = False

    def forbidden_invoke(*args: object, **kwargs: object) -> tuple[object, ...]:
        nonlocal invoked
        invoked = True
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(external_review, "_invoke", forbidden_invoke)

    assert workflow_next(root)["stage"] == "revise"
    with pytest.raises(
        ValueError,
        match="unresolved substantive issues.*external-major-before-rerun",
    ):
        external_review.run_external_review(root, batch_id)
    assert not invoked

    resolve_issue(
        root,
        batch_id,
        issue.issue_id,
        IssueStatus.RESOLVED,
        "The translation was revised and is ready for external recheck.",
    )
    assert workflow_next(root)["stage"] == "external-review"


def test_workflow_does_not_complete_source_only_batch_with_open_blocker(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    units[0] = units[0].model_copy(
        update={
            "kind": UnitKind.CODE,
            "translatable": False,
            "code_language": "python",
        }
    )
    write_jsonl(units_path, units)
    manifest = refresh_batch(root, batch_id)
    assert manifest.translatable_unit_ids == []
    assert verify_extraction(root, "all", force=True)["passed"]
    _audit_and_approve(root, batch_id)
    blocker_path = root / "reviews" / "source-only-blocker.jsonl"
    write_jsonl(
        blocker_path,
        [
            ReviewIssue(
                issue_id="source-only-blocker",
                batch_id=batch_id,
                unit_id=manifest.unit_ids[0],
                severity=Severity.BLOCKER,
                type=IssueType.TECHNICAL,
                explanation="The source-only code unit is substantively incorrect.",
                reviewer="source-only-auditor",
            )
        ],
    )
    import_review(root, batch_id, blocker_path)

    assert workflow_next(root)["stage"] == "revise"


def test_formal_page_render_rejects_unbatched_source_unit(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _audit_and_approve(root, batch_id)
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    source = "print('new source-only unit')"
    unbatched = SourceUnit(
        unit_id="unbatched-code",
        kind=UnitKind.CODE,
        page=1,
        bbox=(570, 40, 610, 100),
        source_text=source,
        source_hash=sha256_text(source),
        translatable=False,
        code_language="python",
        verification_status=SemanticStatus.VERIFIED,
        confidence=1.0,
    )
    write_jsonl(units_path, [*units, unbatched])
    assert verify_extraction(root, "1", force=True)["passed"]

    with pytest.raises(ValueError, match="unbatched_units=.*unbatched-code"):
        render_project(root, "1", "unbatched-formal")
    assert render_project(
        root, "1", "unbatched-draft", allow_draft=True
    )


def test_formal_page_render_rejects_manifest_with_removed_source_unit(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=2, max_words=700)
    assert len(manifests) == 1
    batch_id = manifests[0].batch_id
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    removed_id = units[1].unit_id
    units[1] = units[1].model_copy(update={"translatable": False})
    write_jsonl(units_path, units)
    refreshed = refresh_batch(root, batch_id)
    assert removed_id in refreshed.unit_ids
    assert removed_id not in refreshed.translatable_unit_ids
    _submit(root, batch_id)

    write_jsonl(units_path, [units[0]])

    with pytest.raises(
        ValueError,
        match=rf"manifests reference removed source units.*{removed_id}",
    ):
        render_project(root, "1", "removed-page-unit")


def test_workflow_rejects_completion_with_an_unbatched_interior_unit(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=2, max_words=700)
    assert len(manifests) == 1
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _audit_and_approve(root, batch_id)
    assert workflow_next(root)["stage"] == "complete"
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    source = "print('inserted source-only unit')"
    inserted = SourceUnit(
        unit_id="interior-unbatched-code",
        kind=UnitKind.CODE,
        page=1,
        bbox=(570, 40, 610, 100),
        source_text=source,
        source_hash=sha256_text(source),
        translatable=False,
        code_language="python",
        verification_status=SemanticStatus.VERIFIED,
        confidence=1.0,
    )
    write_jsonl(units_path, [units[0], inserted, *units[1:]])
    assert verify_extraction(root, "all", force=True)["passed"]

    with pytest.raises(
        ValueError, match="unbatched_units=.*interior-unbatched-code"
    ):
        workflow_next(root)

    refreshed = refresh_batch(root, batch_id)
    assert inserted.unit_id in refreshed.unit_ids
    assert workflow_next(root)["stage"] == "qa"


def test_workflow_requires_refresh_when_unit_becomes_translatable(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    units_path = root / "derived" / "units.jsonl"
    unit = read_jsonl(units_path, SourceUnit)[0].model_copy(
        update={"translatable": False}
    )
    write_jsonl(units_path, [unit])
    assert verify_extraction(root, "all", force=True)["passed"]
    refreshed = refresh_batch(root, batch_id)
    assert refreshed.translatable_unit_ids == []
    _audit_and_approve(root, batch_id)
    assert workflow_next(root)["stage"] == "complete"

    write_jsonl(units_path, [unit.model_copy(update={"translatable": True})])
    assert verify_extraction(root, "all", force=True)["passed"]

    with pytest.raises(ValueError, match="stale translatable-unit scope"):
        workflow_next(root)

    refreshed = refresh_batch(root, batch_id)
    assert refreshed.translatable_unit_ids == [unit.unit_id]
    assert workflow_next(root)["stage"] == "translate"


def test_workflow_rejects_manifests_with_removed_source_only_units(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    units_path = root / "derived" / "units.jsonl"
    unit = read_jsonl(units_path, SourceUnit)[0].model_copy(
        update={"translatable": False}
    )
    write_jsonl(units_path, [unit])
    refreshed = refresh_batch(root, batch_id)
    assert refreshed.translatable_unit_ids == []
    write_jsonl(units_path, [])

    with pytest.raises(
        ValueError,
        match=r"reference removed source units.*u001",
    ):
        workflow_next(root)
    with pytest.raises(
        ValueError,
        match=r"reference removed source units.*u001",
    ):
        create_workflow_packet(root, "audit", [batch_id], "fidelity")


def test_external_review_does_not_reuse_approval_after_glossary_change(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _audit_and_approve(root, batch_id)
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
    accepted = ExternalReviewRun(
        run_id="accepted-before-glossary-change",
        batch_id=batch_id,
        reviewer_id="claude",
        driver="claude-code",
        role="primary",
        requested_model="claude-sonnet-5",
        actual_model="claude-sonnet-5",
        model_verified=True,
        translation_fingerprint=external_review.batch_translation_fingerprint(
            root, batch_id
        ),
        packet_sha256="0" * 64,
        prompt_version="test",
        verdict=ExternalReviewVerdict.ACCEPTED,
        summary="No substantive defects.",
        covered_unit_ids=list(manifests[0].unit_ids),
        unit_fingerprints=batch_unit_fingerprints(root, batch_id),
        source_fingerprint=batch_source_fingerprint(root, batch_id),
        structure_fingerprint=batch_structure_fingerprint(root, batch_id),
        context_fingerprint=external_review._external_review_context_fingerprint(
            root,
            batch_id,
            list(manifests[0].unit_ids),
            ReviewScope.FULL,
        ),
    )
    runs_path = root / "reviews" / f"{batch_id}.external-runs.jsonl"
    append_jsonl(runs_path, [accepted])
    assert external_review.run_external_review(root, batch_id)["external_approvable"]

    write_yaml(
        root / "glossary" / "approved.yaml",
        {"terms": [{"source": "architecture", "target": "架构"}]},
    )

    with pytest.raises(ValueError, match="passing, current deterministic QA"):
        external_review.run_external_review(root, batch_id)
    assert read_jsonl(runs_path, ExternalReviewRun) == [accepted]


def test_external_review_context_change_requires_a_new_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    _audit_and_approve(root, batch_id)
    config = load_project(root)
    reviewer = ExternalReviewerConfig(
        id="claude",
        driver="claude-code",
        command="claude",
        model="claude-sonnet-5",
        effort="high",
        fast=False,
    )
    config.external_review = ExternalReviewConfig(reviewers=[reviewer])
    save_project(root, config)
    calls = 0

    def invoke(*args: object, **kwargs: object) -> tuple[object, ...]:
        nonlocal calls
        calls += 1
        return (
            {"verdict": "accepted", "summary": "No defects.", "issues": []},
            "{}",
            reviewer.model,
            reviewer.effort,
            reviewer.model,
            "off",
            1,
            PromptDelivery.FILE,
            1.0,
            ReviewUsage(input_tokens=100, provider_turns=1),
            0.01,
        )

    monkeypatch.setattr(external_review, "_invoke", invoke)
    monkeypatch.setattr(external_review, "_command_version", lambda command: "test")

    assert external_review.run_external_review(root, batch_id)[
        "external_approvable"
    ]
    first_run = read_jsonl(
        root / "reviews" / f"{batch_id}.external-runs.jsonl",
        ExternalReviewRun,
    )[-1]
    assert first_run.context_fingerprint

    style_path = root / "context" / "style-guide.md"
    atomic_write_text(
        style_path,
        style_path.read_text(encoding="utf-8").rstrip()
        + "\n\n- Newly mandatory external-review instruction.\n",
    )
    empty = root / "reviews" / "context-refresh.jsonl"
    write_jsonl(empty, [])
    import_review(root, batch_id, empty)

    assert not external_review_status(root, batch_id)["external_approvable"]
    assert external_review.run_external_review(root, batch_id)[
        "external_approvable"
    ]
    runs = read_jsonl(
        root / "reviews" / f"{batch_id}.external-runs.jsonl",
        ExternalReviewRun,
    )
    assert calls == 2
    assert len(runs) == 2
    assert runs[0].context_fingerprint != runs[1].context_fingerprint


def test_renderer_owned_caption_separator_is_semantic_noop(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    units[0] = units[0].model_copy(update={"kind": UnitKind.CAPTION})
    write_jsonl(units_path, units)
    unit = units[0]
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


def test_caption_like_paragraph_separator_change_is_semantic_revision(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    unit = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)[0]
    input_path = root / "batches" / batch_id / "paragraph-initial.jsonl"
    write_jsonl(
        input_path,
        [
            TranslationRecord(
                unit_id=unit.unit_id,
                target_text="图 1。说明了普通段落中的引用。",
                source_hash=unit.source_hash,
            )
        ],
    )
    submit_translation(root, batch_id, input_path)
    current = translation_map(root)[unit.unit_id]
    write_jsonl(
        input_path,
        [
            current.model_copy(
                update={"target_text": "图 1 说明了普通段落中的引用。"}
            )
        ],
    )

    returned = submit_translation(root, batch_id, input_path)

    assert returned[0].revision == 2
    assert returned[0].target_text == "图 1 说明了普通段落中的引用。"
    assert len(
        read_jsonl(root / "translations" / "history.jsonl", TranslationRecord)
    ) == 2


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


def test_dependency_closure_invalidates_only_reached_batch_seams(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, pages=11, max_words=700)
    assert [len(manifest.unit_ids) for manifest in manifests] == [5, 5, 1]
    middle = manifests[1]

    assert dependency_closure(root, [middle.batch_id], ["u008"]) == [
        "u007",
        "u008",
        "u009",
    ]
    assert dependency_closure(root, [middle.batch_id], ["u007"]) == [
        "u005",
        "u006",
        "u007",
        "u008",
    ]
    assert dependency_closure(root, [middle.batch_id], ["u009"]) == [
        "u008",
        "u009",
        "u010",
        "u011",
    ]


def test_dependency_closure_composes_sidebar_and_continuation_dependencies(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=4, max_words=700)
    assert len(manifests) == 1
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    units[1] = units[1].model_copy(
        update={
            "kind": UnitKind.HEADING,
            "sidebar_id": "composed-sidebar",
            "sidebar_role": SidebarRole.TITLE,
        }
    )
    units[2] = units[2].model_copy(
        update={
            "sidebar_id": "composed-sidebar",
            "sidebar_role": SidebarRole.BODY,
            "continued_to_next": True,
        }
    )
    units[3] = units[3].model_copy(update={"continues_from_previous": True})
    write_jsonl(units_path, units)

    closure = dependency_closure(root, [manifests[0].batch_id], [units[0].unit_id])

    assert closure == [unit.unit_id for unit in units]


def test_page_receipts_skip_unchanged_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _make_project(tmp_path, 1)
    receipt_path = root / "evidence" / "pages" / "page-0001.json"
    before = receipt_path.read_bytes()

    def fail_pixmap(*args: object, **kwargs: object) -> object:
        raise AssertionError("cached verification attempted to rasterize the PDF")

    monkeypatch.setattr(fitz.Page, "get_pixmap", fail_pixmap)
    require_verified_extraction(root, {1})
    assert receipt_path.read_bytes() == before


def test_partial_verification_report_keeps_cached_pages(tmp_path: Path) -> None:
    root, _ = _make_project(tmp_path, 2)
    (root / "evidence" / "pages" / "page-0002.json").unlink()

    result = verify_extraction(root, "all")
    report = Path(result["visual_report"]).read_text(encoding="utf-8")

    assert result["cached_pages"] == [1]
    assert result["verified_pages"] == [2]
    assert "PDF p.1" in report
    assert "PDF p.2" in report


def test_cache_hit_verification_persists_current_result(tmp_path: Path) -> None:
    root, _ = _make_project(tmp_path, 2)
    issue = ExtractionIssue(
        issue_id="page-two-blocker",
        page=2,
        severity=Severity.BLOCKER,
        code="page-two-defect",
        message="Only the second page is defective.",
    )
    write_jsonl(root / "derived" / "extraction-issues.jsonl", [issue])

    failed = verify_extraction(root, "2")
    assert not failed["passed"]
    assert read_json(root / "derived" / "verification.json")["passed"] is False

    cached = verify_extraction(root, "1")
    persisted = read_json(root / "derived" / "verification.json")

    assert cached["passed"]
    assert cached["cached_pages"] == [1]
    assert cached["verified_pages"] == []
    assert persisted == cached
    assert persisted["pages"] == [
        {
            "page": 1,
            "unit_count": 1,
            "token_coverage": persisted["pages"][0]["token_coverage"],
            "cached": True,
        }
    ]


def test_schema_v3_rejects_new_v4_evidence(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    assert run_qa(root, batch_id).passed
    qa_path = root / "qa" / f"{batch_id}.json"
    verification_path = root / "derived" / "verification.json"
    verification_before = verification_path.read_bytes()
    config = load_project(root)
    config.schema_version = 3
    save_project(root, config)
    write_json(
        qa_path,
        {
            "schema_version": 1,
            "batch_id": batch_id,
            "passed": True,
            "translation_fingerprint": "legacy-v3-fingerprint",
            "errors": [],
            "warnings": [],
        },
    )
    qa_before = qa_path.read_bytes()

    assert not qa_report_is_current(root, batch_id)
    assert workflow_next(root)["stage"] == "qa"
    with pytest.raises(ValueError, match="project migrate"):
        run_qa(root, batch_id)
    with pytest.raises(ValueError, match="project migrate"):
        verify_extraction(root, "1", force=True)

    assert qa_path.read_bytes() == qa_before
    assert verification_path.read_bytes() == verification_before
    assert load_project(root).schema_version == 3


def test_cached_pages_remain_in_requested_global_semantic_checks(
    tmp_path: Path,
) -> None:
    root, _ = _make_project(tmp_path, 2)
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    units[1] = units[1].model_copy(update={"unit_id": units[0].unit_id})
    write_jsonl(units_path, units)

    result = verify_extraction(root, "all")

    assert not result["passed"]
    assert result["cached_pages"] == [1]
    assert result["verified_pages"] == [2]
    assert {error["code"] for error in result["errors"]} == {
        "duplicate-unit-id"
    }
    failed_receipt = PageVerificationReceipt.model_validate(
        read_json(root / "evidence" / "pages" / "page-0002.json")
    )
    assert not failed_receipt.passed
    assert {error["code"] for error in failed_receipt.errors} == {
        "duplicate-unit-id"
    }


def test_fully_cached_page_still_runs_project_global_semantic_checks(
    tmp_path: Path,
) -> None:
    root, _ = _make_project(tmp_path, 2)
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    original_second_id = units[1].unit_id
    units[1] = units[1].model_copy(update={"unit_id": units[0].unit_id})
    write_jsonl(units_path, units)

    result = verify_extraction(root, "1")

    assert not result["passed"]
    assert result["cached_pages"] == [1]
    assert result["verified_pages"] == []
    assert {error["code"] for error in result["errors"]} == {
        "duplicate-unit-id"
    }
    with pytest.raises(ValueError, match="duplicate-unit-id"):
        require_verified_extraction(root, {1})
    receipt = PageVerificationReceipt.model_validate(
        read_json(root / "evidence" / "pages" / "page-0001.json")
    )
    assert receipt.passed

    units[1] = units[1].model_copy(update={"unit_id": original_second_id})
    write_jsonl(units_path, units)
    recovered = verify_extraction(root, "1")
    assert recovered["passed"]
    assert recovered["cached_pages"] == [1]


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


def test_partial_page_verification_inherits_cross_page_blocker(
    tmp_path: Path,
) -> None:
    root, _ = _make_project(tmp_path, 2)
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    units[0] = units[0].model_copy(
        update={
            "kind": UnitKind.HEADING,
            "sidebar_id": "cross-page",
            "sidebar_role": SidebarRole.TITLE,
        }
    )
    units[1] = units[1].model_copy(
        update={"sidebar_id": "cross-page", "sidebar_role": SidebarRole.BODY}
    )
    write_jsonl(units_path, units)
    assert verify_extraction(root, "all", force=True)["passed"]
    issue = ExtractionIssue(
        issue_id="cross-page-blocker",
        page=1,
        unit_id=units[0].unit_id,
        severity=Severity.BLOCKER,
        code="cross-page-defect",
        message="The first sidebar fragment invalidates every dependent page.",
    )
    write_jsonl(root / "derived" / "extraction-issues.jsonl", [issue])

    result = verify_extraction(root, "2")

    assert not result["passed"]
    assert result["cached_pages"] == []
    assert result["verified_pages"] == [2]
    assert {error["code"] for error in result["errors"]} == {
        "open-extraction-issue"
    }
    receipt = PageVerificationReceipt.model_validate(
        read_json(root / "evidence" / "pages" / "page-0002.json")
    )
    assert not receipt.passed


def test_page_evidence_closes_continuation_and_sidebar_dependencies(
    tmp_path: Path,
) -> None:
    root, _ = _make_project(tmp_path, 3)
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    units[0] = units[0].model_copy(update={"continued_to_next": True})
    units[1] = units[1].model_copy(
        update={
            "kind": UnitKind.HEADING,
            "sidebar_id": "continued-sidebar",
            "sidebar_role": SidebarRole.TITLE,
        }
    )
    units[2] = units[2].model_copy(
        update={
            "sidebar_id": "continued-sidebar",
            "sidebar_role": SidebarRole.BODY,
        }
    )
    write_jsonl(units_path, units)

    assert [unit.unit_id for unit in page_evidence_units(1, units)] == [
        unit.unit_id for unit in units
    ]
    assert [unit.unit_id for unit in page_evidence_units(2, units)] == [
        unit.unit_id for unit in units
    ]
    assert verify_extraction(root, "all", force=True)["passed"]
    replacement = "A changed final sidebar fragment."
    units[2] = units[2].model_copy(
        update={"source_text": replacement, "source_hash": sha256_text(replacement)}
    )
    write_jsonl(units_path, units)

    refreshed = verify_extraction(root, "1")

    assert refreshed["passed"]
    assert refreshed["cached_pages"] == []
    assert refreshed["verified_pages"] == [1]


def test_partial_verification_failure_preserves_clean_page_receipt(
    tmp_path: Path,
) -> None:
    root, _ = _make_project(tmp_path, 2)
    issue = ExtractionIssue(
        issue_id="page-one-blocker",
        page=1,
        severity=Severity.BLOCKER,
        code="page-one-defect",
        message="Only the first page is defective.",
    )
    write_jsonl(root / "derived" / "extraction-issues.jsonl", [issue])

    result = verify_extraction(root, "all", force=True)
    failed_receipt = PageVerificationReceipt.model_validate(
        read_json(root / "evidence" / "pages" / "page-0001.json")
    )
    clean_receipt = PageVerificationReceipt.model_validate(
        read_json(root / "evidence" / "pages" / "page-0002.json")
    )
    assert not result["passed"]
    assert not failed_receipt.passed
    assert {error["code"] for error in failed_receipt.errors} == {
        "open-extraction-issue"
    }
    assert clean_receipt.passed
    assert clean_receipt.errors == []
    assert verify_extraction(root, "2")["cached_pages"] == [2]


def test_sidebar_error_is_scoped_to_dependent_page_receipts(tmp_path: Path) -> None:
    root, _ = _make_project(tmp_path, 3)
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    for index in (0, 1):
        units[index] = units[index].model_copy(
            update={"sidebar_id": "cross-page", "sidebar_role": SidebarRole.BODY}
        )
    write_jsonl(units_path, units)

    result = verify_extraction(root, "all", force=True)
    receipts = [
        PageVerificationReceipt.model_validate(
            read_json(root / "evidence" / "pages" / f"page-{page:04}.json")
        )
        for page in (1, 2, 3)
    ]
    assert not result["passed"]
    assert all(not receipt.passed for receipt in receipts[:2])
    assert all(
        {error["code"] for error in receipt.errors} == {"invalid-sidebar-title"}
        for receipt in receipts[:2]
    )
    assert receipts[2].passed
    assert receipts[2].errors == []
    assert verify_extraction(root, "3")["cached_pages"] == [3]


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


def test_workflow_packet_rejects_overlapping_batch_manifests(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=1)
    duplicate = create_batches(
        root, "1", max_words=700, prefix="duplicate-scope"
    )[0]
    overlapping_ids = {manifests[0].batch_id, duplicate.batch_id}
    ordered = [
        manifest.batch_id
        for manifest in create_workflow_packet.__globals__["_all_manifests"](
            root
        )
        if manifest.batch_id in overlapping_ids
    ]
    assert set(ordered) == overlapping_ids

    next_action = workflow_next(root)
    assert next_action["stage"] == "translate"
    assert len(next_action["batch_ids"]) == 1
    assert next_action["batch_ids"][0] in overlapping_ids
    recommended = create_workflow_packet(
        root, next_action["stage"], next_action["batch_ids"]
    )
    assert recommended.batch_ids == next_action["batch_ids"]

    with pytest.raises(ValueError, match="overlapping source units"):
        create_workflow_packet(root, "translate", ordered)


def test_audit_packet_includes_all_rendered_structured_translation_fields(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=1)
    manifest = manifests[0]
    units_path = root / "derived" / "units.jsonl"
    unit = read_jsonl(units_path, SourceUnit)[0].model_copy(
        update={
            "kind": UnitKind.FIGURE,
            "figure_labels": [
                FigureLabel(source="Status", target="来源单元旧译")
            ],
            "visual_text_status": SemanticStatus.VERIFIED,
        }
    )
    write_jsonl(units_path, [unit])
    verification = verify_extraction(root, "all", force=True)
    assert verification["passed"], verification["errors"]
    refresh_batch(root, manifest.batch_id)
    input_path = root / "batches" / manifest.batch_id / "structured.jsonl"
    write_jsonl(
        input_path,
        [
            TranslationRecord(
                unit_id=unit.unit_id,
                target_text="状态图",
                figure_labels=[
                    FigureLabel(source="Status", target="译文记录新译")
                ],
                reader_note=ReaderNote(
                    text="该状态已由规范勘误更新。",
                    sources=["https://example.com/erratum"],
                    accessed_at="2026-08-09",
                ),
                source_hash=unit.source_hash,
            )
        ],
    )
    submit_translation(root, manifest.batch_id, input_path)
    assert run_qa(root, manifest.batch_id).passed

    packet = create_workflow_packet(
        root, "audit", [manifest.batch_id], "fidelity"
    )
    audit_text = (root / packet.files[f"{manifest.batch_id}:audit"]).read_text(
        encoding="utf-8"
    )

    assert "Figure label sources:\n- Status" in audit_text
    assert "Figure label translations:\n- Status: 译文记录新译" in audit_text
    assert "来源单元旧译" not in audit_text
    assert "Reader note (separate from translated body):" in audit_text
    assert "该状态已由规范勘误更新。" in audit_text
    assert "https://example.com/erratum" in audit_text
    assert "Accessed: 2026-08-09" in audit_text

    outputs = render_project(
        root, "1", "structured-fields-draft", allow_draft=True
    )
    rendered_markdown = Path(outputs["markdown"]).read_text(encoding="utf-8")
    rendered_html = Path(outputs["html"]).read_text(encoding="utf-8")
    assert "译文记录新译" in rendered_markdown
    assert "译文记录新译" in rendered_html
    assert "来源单元旧译" not in rendered_markdown
    assert "来源单元旧译" not in rendered_html

    issues = root / "packets" / packet.packet_id / "issues.jsonl"
    write_jsonl(issues, [])
    import_review_set(
        root,
        root / "packets" / packet.packet_id / "manifest.json",
        issues,
    )
    assert audit_coverage(root, manifest.batch_id)["missing"]["fidelity"] == []


def test_audit_packet_includes_source_only_figure_label_targets(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=1)
    manifest = manifests[0]
    units_path = root / "derived" / "units.jsonl"
    unit = read_jsonl(units_path, SourceUnit)[0].model_copy(
        update={
            "kind": UnitKind.FIGURE,
            "translatable": False,
            "figure_labels": [FigureLabel(source="Ready", target="就绪")],
            "visual_text_status": SemanticStatus.VERIFIED,
        }
    )
    write_jsonl(units_path, [unit])
    verification = verify_extraction(root, "all", force=True)
    assert verification["passed"], verification["errors"]
    refresh_batch(root, manifest.batch_id)
    assert run_qa(root, manifest.batch_id).passed

    packet = create_workflow_packet(
        root, "audit", [manifest.batch_id], "technical"
    )
    audit_text = (root / packet.files[f"{manifest.batch_id}:audit"]).read_text(
        encoding="utf-8"
    )

    assert "[source-only]" in audit_text
    assert "Figure label sources:\n- Ready" in audit_text
    assert "Figure label translations:\n- Ready: 就绪" in audit_text


def test_review_set_rejects_manifest_that_differs_from_canonical_packet(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=2, max_words=700)
    batch = manifests[0]
    _submit(root, batch.batch_id)
    assert run_qa(root, batch.batch_id).passed
    fingerprints = batch_unit_fingerprints(root, batch.batch_id)
    first_id, second_id = batch.unit_ids
    canonical = WorkflowPacketManifest(
        packet_id="canonical-binding",
        stage="audit",
        batch_ids=[batch.batch_id],
        lens="fidelity",
        unit_ids=[first_id],
        unit_fingerprints={first_id: fingerprints[first_id]},
        files={},
        total_bytes=0,
    )
    canonical, _ = _store_manual_audit_packet(root, canonical)
    forged = canonical.model_copy(
        update={
            "unit_ids": list(batch.unit_ids),
            "unit_fingerprints": {
                first_id: fingerprints[first_id],
                second_id: fingerprints[second_id],
            },
        }
    )
    forged_path = root / "reviews" / "forged-packet-manifest.json"
    write_json(forged_path, forged.model_dump(mode="json"))
    issues_path = root / "reviews" / "forged-packet-issues.jsonl"
    write_jsonl(issues_path, [])

    with pytest.raises(ValueError, match="does not match the canonical stored manifest"):
        import_review_set(root, forged_path, issues_path)

    assert audit_coverage(root, batch.batch_id)["missing"]["fidelity"] == sorted(
        batch.unit_ids
    )


def test_review_set_rejects_packet_file_changed_after_creation(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=1)
    batch = manifests[0]
    _submit(root, batch.batch_id)
    assert run_qa(root, batch.batch_id).passed
    packet = create_workflow_packet(
        root, "audit", [batch.batch_id], "fidelity"
    )
    audit_path = root / packet.files[f"{batch.batch_id}:audit"]
    audit_path.write_text(
        audit_path.read_text(encoding="utf-8").replace(
            "这是经过技术审校的中文译文", "[translation omitted after packet creation]"
        ),
        encoding="utf-8",
    )
    issues_path = root / "packets" / packet.packet_id / "issues.jsonl"
    write_jsonl(issues_path, [])

    with pytest.raises(ValueError, match="packet file digest mismatch"):
        import_review_set(
            root,
            root / "packets" / packet.packet_id / "manifest.json",
            issues_path,
        )

    assert audit_coverage(root, batch.batch_id)["missing"]["fidelity"] == sorted(
        batch.unit_ids
    )


def test_review_set_rejects_context_changed_after_packet_creation(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=1)
    batch = manifests[0]
    _submit(root, batch.batch_id)
    assert run_qa(root, batch.batch_id).passed
    packet = create_workflow_packet(
        root, "audit", [batch.batch_id], "fidelity"
    )
    issues_path = root / "packets" / packet.packet_id / "issues.jsonl"
    write_jsonl(issues_path, [])
    style_path = root / "context" / "style-guide.md"
    atomic_write_text(
        style_path,
        style_path.read_text(encoding="utf-8").rstrip()
        + "\n\n- Newly mandatory terminology review.\n",
    )

    with pytest.raises(ValueError, match="Audit packet context is stale"):
        import_review_set(
            root,
            root / "packets" / packet.packet_id / "manifest.json",
            issues_path,
        )

    assert audit_coverage(root, batch.batch_id)["missing"]["fidelity"] == sorted(
        batch.unit_ids
    )


def test_review_set_preserves_explicitly_empty_batch_coverage(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 2)
    for manifest in manifests:
        _submit(root, manifest.batch_id)
        assert run_qa(root, manifest.batch_id).passed
    first, second = manifests
    second_fingerprints = batch_unit_fingerprints(root, second.batch_id)
    packet = WorkflowPacketManifest(
        packet_id="explicit-empty-coverage",
        stage="audit",
        batch_ids=[first.batch_id, second.batch_id],
        lens="fidelity",
        unit_ids=list(second.unit_ids),
        unit_fingerprints=second_fingerprints,
        files={},
        total_bytes=0,
    )
    packet, manifest_path = _store_manual_audit_packet(root, packet)
    packet_dir = manifest_path.parent
    issues_path = packet_dir / "issues.jsonl"
    write_jsonl(issues_path, [])

    current = translation_map(root)
    first_id = first.translatable_unit_ids[0]
    current[first_id] = current[first_id].model_copy(
        update={
            "target_text": current[first_id].target_text + "修订",
            "revision": 2,
            "status": ProjectStatus.REVISED,
        }
    )
    write_jsonl(root / "translations" / "current.jsonl", current.values())

    import_review_set(root, manifest_path, issues_path)

    assert audit_coverage(root, first.batch_id)["missing"]["fidelity"] == [
        first_id
    ]
    assert audit_coverage(root, second.batch_id)["missing"]["fidelity"] == []


def test_review_set_rejects_covered_unit_without_packet_fingerprint(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    assert run_qa(root, batch_id).passed
    packet = create_workflow_packet(root, "audit", [batch_id], "fidelity")
    covered_id = packet.unit_ids[0]
    manifest_path = root / "packets" / packet.packet_id / "manifest.json"
    manifest_payload = read_json(manifest_path)
    manifest_payload["unit_fingerprints"].pop(covered_id)
    write_json(manifest_path, manifest_payload)
    issues_path = root / "packets" / packet.packet_id / "issues.jsonl"
    write_jsonl(issues_path, [])

    _submit(root, batch_id, suffix="修订")

    with pytest.raises(
        ValueError, match=f"missing fingerprints for covered units.*{covered_id}"
    ):
        import_review_set(root, manifest_path, issues_path)
    assert audit_coverage(root, batch_id)["missing"]["fidelity"] == [
        covered_id
    ]


def test_review_set_revalidates_packet_inside_the_audit_import_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    assert run_qa(root, batch_id).passed
    packet = create_workflow_packet(root, "audit", [batch_id], "fidelity")
    manifest_path = root / "packets" / packet.packet_id / "manifest.json"
    issues_path = manifest_path.parent / "issues.jsonl"
    write_jsonl(issues_path, [])
    original_lock = import_review_set.__globals__["project_write_lock"]
    raced = False

    @contextmanager
    def racing_lock(lock_root: Path):
        nonlocal raced
        with original_lock(lock_root):
            if not raced:
                raced = True
                current = translation_map(root)
                unit_id = manifests[0].translatable_unit_ids[0]
                current[unit_id] = current[unit_id].model_copy(
                    update={
                        "target_text": current[unit_id].target_text + "并发修订",
                        "revision": current[unit_id].revision + 1,
                        "status": ProjectStatus.REVISED,
                    }
                )
                write_jsonl(
                    root / "translations" / "current.jsonl", current.values()
                )
            yield

    monkeypatch.setitem(
        import_review_set.__globals__, "project_write_lock", racing_lock
    )

    with pytest.raises(ValueError, match="Audit packet is stale for units"):
        import_review_set(root, manifest_path, issues_path)

    assert raced
    assert audit_coverage(root, batch_id)["missing"]["fidelity"] == sorted(
        manifests[0].unit_ids
    )
    assert read_jsonl(
        root / "evidence" / "audits" / f"{batch_id}.jsonl", AuditRun
    ) == []


def test_review_set_validates_all_batches_before_applying(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 2)
    first, second = manifests
    for batch in manifests:
        _submit(root, batch.batch_id)
        assert run_qa(root, batch.batch_id).passed

    existing_issue = ReviewIssue(
        issue_id="later-batch-conflict",
        batch_id=second.batch_id,
        unit_id=second.unit_ids[0],
        severity=Severity.MAJOR,
        type=IssueType.MEANING,
        explanation="Previously imported issue content.",
        reviewer="independent-fidelity-auditor",
    )
    existing_path = root / "reviews" / "existing-second-batch.jsonl"
    write_jsonl(existing_path, [existing_issue])
    import_review(root, second.batch_id, existing_path, lenses=[])

    packet = create_workflow_packet(
        root, "audit", [first.batch_id, second.batch_id], "fidelity"
    )
    manifest_path = root / "packets" / packet.packet_id / "manifest.json"
    issues_path = manifest_path.parent / "issues.jsonl"
    first_issue = ReviewIssue(
        issue_id="first-batch-fresh",
        batch_id=first.batch_id,
        unit_id=first.unit_ids[0],
        severity=Severity.MAJOR,
        type=IssueType.MEANING,
        explanation="This issue is valid but must not be imported yet.",
        reviewer="independent-fidelity-auditor",
    )
    conflicting_issue = existing_issue.model_copy(
        update={"explanation": "Conflicting replacement issue content."}
    )
    write_jsonl(issues_path, [first_issue, conflicting_issue])

    current_before = (root / "translations" / "current.jsonl").read_bytes()
    project_before = (root / "project.yaml").read_bytes()
    first_summary = root / "reviews" / f"{first.batch_id}.audit.json"
    assert not first_summary.exists()

    with pytest.raises(
        ValueError, match="issue IDs already exist with different content"
    ):
        import_review_set(root, manifest_path, issues_path)

    assert read_jsonl(
        root / "reviews" / f"{first.batch_id}.issues.jsonl", ReviewIssue
    ) == []
    assert read_jsonl(
        root / "evidence" / "audits" / f"{first.batch_id}.jsonl", AuditRun
    ) == []
    assert not first_summary.exists()
    assert (root / "translations" / "current.jsonl").read_bytes() == current_before
    assert (root / "project.yaml").read_bytes() == project_before


def test_review_set_rejects_packet_id_path_escape(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    assert run_qa(root, batch_id).passed
    packet = create_workflow_packet(root, "audit", [batch_id], "fidelity")
    manifest_path = root / "packets" / packet.packet_id / "manifest.json"
    payload = read_json(manifest_path)
    payload["packet_id"] = "../escaped"
    write_json(manifest_path, payload)
    issues_path = manifest_path.parent / "issues.jsonl"
    write_jsonl(issues_path, [])

    with pytest.raises(ValueError, match="packet_id"):
        import_review_set(root, manifest_path, issues_path)
    assert not (root / "escaped").exists()


def test_review_set_rejects_issue_outside_packet_unit_coverage(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=2, max_words=700)
    assert len(manifests) == 1
    batch = manifests[0]
    _submit(root, batch.batch_id)
    assert run_qa(root, batch.batch_id).passed
    fingerprints = batch_unit_fingerprints(root, batch.batch_id)
    covered_id, omitted_id = batch.unit_ids
    packet = WorkflowPacketManifest(
        packet_id="issue-outside-coverage",
        stage="audit",
        batch_ids=[batch.batch_id],
        lens="fidelity",
        unit_ids=[covered_id],
        unit_fingerprints={covered_id: fingerprints[covered_id]},
        files={},
        total_bytes=0,
    )
    packet, manifest_path = _store_manual_audit_packet(root, packet)
    packet_dir = manifest_path.parent
    issues_path = packet_dir / "issues.jsonl"
    issue = ReviewIssue(
        issue_id="hallucinated-omitted-unit",
        batch_id=batch.batch_id,
        unit_id=omitted_id,
        severity=Severity.MAJOR,
        type=IssueType.MEANING,
        explanation="This unit was not included in the incremental audit packet.",
        reviewer="independent-fidelity-auditor",
    )
    write_jsonl(issues_path, [issue])

    with pytest.raises(
        ValueError, match=f"outside the audit packet coverage.*{omitted_id}"
    ):
        import_review_set(root, manifest_path, issues_path)
    assert read_jsonl(
        root / "reviews" / f"{batch.batch_id}.issues.jsonl", ReviewIssue
    ) == []
    assert audit_coverage(root, batch.batch_id)["missing"]["fidelity"] == sorted(
        batch.unit_ids
    )


def test_review_import_preserves_explicitly_empty_lenses(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    empty = root / "reviews" / "no-lenses.jsonl"
    write_jsonl(empty, [])

    import_review(root, batch_id, empty, lenses=[])

    coverage = audit_coverage(root, batch_id)
    assert not coverage["complete"]
    assert all(not unit_ids for unit_ids in coverage["coverage"].values())


def test_review_import_rejects_conflicting_existing_issue_id(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    unit_id = manifests[0].unit_ids[0]
    _submit(root, batch_id)
    assert run_qa(root, batch_id).passed
    blocker = ReviewIssue(
        issue_id="shared-audit-r001",
        batch_id=batch_id,
        unit_id=unit_id,
        severity=Severity.BLOCKER,
        type=IssueType.MEANING,
        explanation="The fidelity reviewer found a blocking omission.",
        reviewer="independent-fidelity-auditor",
    )
    blocker_path = root / "reviews" / "fidelity-blocker.jsonl"
    write_jsonl(blocker_path, [blocker])
    import_review(root, batch_id, blocker_path, lenses=["fidelity"])
    import_review(root, batch_id, blocker_path, lenses=["fidelity"])
    conflicting = blocker.model_copy(
        update={
            "severity": Severity.SUGGESTION,
            "explanation": "A different lens reused the same identifier.",
            "reviewer": "independent-technical-auditor",
        }
    )
    conflicting_path = root / "reviews" / "technical-collision.jsonl"
    write_jsonl(conflicting_path, [conflicting])

    with pytest.raises(
        ValueError,
        match="already exist with different content.*shared-audit-r001",
    ):
        import_review(root, batch_id, conflicting_path, lenses=["technical"])

    ledger = read_jsonl(
        root / "reviews" / f"{batch_id}.issues.jsonl", ReviewIssue
    )
    assert ledger == [blocker]
    assert audit_coverage(root, batch_id)["missing"]["technical"] == [
        unit_id
    ]


def test_audit_packet_emits_out_of_set_seam_neighbors_as_read_only_context(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path)
    for manifest in manifests:
        _submit(root, manifest.batch_id)
        assert run_qa(root, manifest.batch_id).passed
    middle = manifests[1]

    packet = create_workflow_packet(root, "audit", [middle.batch_id], "fidelity")

    assert packet.unit_ids == middle.unit_ids
    assert set(packet.unit_fingerprints) == {"u001", "u002", "u003"}
    context_path = root / packet.files["audit:read-only-context"]
    context = context_path.read_text(encoding="utf-8")
    assert "outside the requested batch set" in context
    assert "## u001" in context
    assert "## u003" in context
    audit_path = root / packet.files[f"{middle.batch_id}:audit"]
    audit = audit_path.read_text(encoding="utf-8")
    assert "## u002" in audit
    assert "## u001" not in audit
    assert "## u003" not in audit

    issues = root / "packets" / packet.packet_id / "issues.jsonl"
    write_jsonl(issues, [])
    import_review_set(root, root / "packets" / packet.packet_id / "manifest.json", issues)
    assert audit_coverage(root, middle.batch_id)["missing"]["fidelity"] == []
    assert audit_coverage(root, manifests[0].batch_id)["missing"]["fidelity"] == [
        "u001"
    ]
    assert audit_coverage(root, manifests[2].batch_id)["missing"]["fidelity"] == [
        "u003"
    ]

    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    changed_source = units[0].source_text + " changed seam"
    units[0] = units[0].model_copy(
        update={
            "source_text": changed_source,
            "source_hash": sha256_text(changed_source),
        }
    )
    write_jsonl(units_path, units)

    assert audit_coverage(root, middle.batch_id)["missing"]["fidelity"] == [
        "u002"
    ]


def test_review_packets_show_renderer_visible_equations(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    units_path = root / "derived" / "units.jsonl"
    original = read_jsonl(units_path, SourceUnit)[0]
    unit = original.model_copy(
        update={
            "kind": UnitKind.EQUATION,
            "translatable": False,
            "latex": r"E = mc^2",
            "equation_number": "7.3",
            "math_status": SemanticStatus.VERIFIED,
        }
    )
    write_jsonl(units_path, [unit])
    refresh_batch(root, batch_id)
    expected = "$$\nE = mc^2 \\tag{7.3}\n$$"

    packet = create_workflow_packet(root, "audit", [batch_id], "technical")
    audit_text = (root / packet.files[f"{batch_id}:audit"]).read_text(
        encoding="utf-8"
    )
    external_text = external_review._packet_text(root, batch_id)[0]
    evidence_source = external_review._evidence_map(root, batch_id)[unit.unit_id][0]

    assert expected in audit_text
    assert expected in external_text
    assert expected in evidence_source


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
        context_fingerprint=external_review._external_review_context_fingerprint(
            root,
            manifest.batch_id,
            list(snapshot),
            ReviewScope.FULL,
        ),
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


def test_incremental_external_review_rejects_inconclusive_base_chain(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, pages=5, max_words=700)
    assert len(manifests) == 1
    manifest = manifests[0]
    _submit(root, manifest.batch_id)
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
            ),
            ExternalReviewerConfig(
                id="antigravity",
                driver="antigravity",
                command="antigravity",
                model="gemini-3.1-pro",
                effort="high",
            ),
        ]
    )
    save_project(root, config)
    suggestion = ReviewIssue(
        issue_id="uncertain-base-suggestion",
        batch_id=manifest.batch_id,
        unit_id=manifest.unit_ids[0],
        severity=Severity.SUGGESTION,
        type=IssueType.STYLE,
        explanation="This low-confidence suggestion requires a second opinion.",
        confidence=0.2,
        reviewer="external:claude",
    )
    write_jsonl(
        root / "reviews" / f"{manifest.batch_id}.issues.jsonl", [suggestion]
    )
    snapshot = batch_unit_fingerprints(root, manifest.batch_id)
    primary = ExternalReviewRun(
        run_id="inconclusive-chain-primary",
        batch_id=manifest.batch_id,
        reviewer_id="claude",
        driver="claude-code",
        role="primary",
        requested_model="claude-sonnet-5",
        actual_model="claude-sonnet-5",
        model_verified=True,
        translation_fingerprint="old-fingerprint",
        packet_sha256="0" * 64,
        prompt_version="test",
        verdict=ExternalReviewVerdict.ACCEPTED,
        summary="Accepted subject to a required second opinion.",
        issue_ids=[suggestion.issue_id],
        covered_unit_ids=list(snapshot),
        unit_fingerprints=snapshot,
        source_fingerprint=batch_source_fingerprint(root, manifest.batch_id),
        structure_fingerprint=batch_structure_fingerprint(root, manifest.batch_id),
        context_fingerprint=external_review._external_review_context_fingerprint(
            root,
            manifest.batch_id,
            list(snapshot),
            ReviewScope.FULL,
        ),
    )
    second = primary.model_copy(
        update={
            "run_id": "inconclusive-chain-second",
            "reviewer_id": "antigravity",
            "driver": ExternalReviewDriver.ANTIGRAVITY,
            "role": "second-opinion",
            "requested_model": "gemini-3.1-pro",
            "actual_model": "gemini-3.1-pro",
            "base_run_id": primary.run_id,
            "verdict": ExternalReviewVerdict.INCONCLUSIVE,
            "summary": "The second opinion was inconclusive.",
            "issue_ids": [],
        }
    )
    append_jsonl(
        root / "reviews" / f"{manifest.batch_id}.external-runs.jsonl",
        [primary, second],
    )
    current = translation_map(root)
    changed_id = manifest.translatable_unit_ids[2]
    current[changed_id] = current[changed_id].model_copy(
        update={"target_text": "局部修订", "revision": 2}
    )
    write_jsonl(root / "translations" / "current.jsonl", current.values())
    current_snapshot = batch_unit_fingerprints(root, manifest.batch_id)
    inherited = primary.model_copy(
        update={
            "run_id": "accepted-incremental-from-inconclusive-base",
            "translation_fingerprint": external_review.batch_translation_fingerprint(
                root, manifest.batch_id
            ),
            "scope": ReviewScope.INCREMENTAL,
            "base_run_id": primary.run_id,
            "issue_ids": [],
            "covered_unit_ids": [changed_id],
            "unit_fingerprints": current_snapshot,
            "context_fingerprint": (
                external_review._external_review_context_fingerprint(
                    root,
                    manifest.batch_id,
                    [changed_id],
                    ReviewScope.INCREMENTAL,
                )
            ),
        }
    )
    append_jsonl(
        root / "reviews" / f"{manifest.batch_id}.external-runs.jsonl",
        [inherited],
    )

    status = external_review_status(root, manifest.batch_id)
    assert status["verdict"] == ExternalReviewVerdict.INCONCLUSIVE
    assert not status["external_approvable"]

    scope, base, covered, reviewer = _primary_review_scope(
        root, manifest.batch_id, None
    )
    assert scope is ReviewScope.FULL
    assert base is None
    assert covered == manifest.unit_ids
    assert reviewer is None


def test_incremental_external_packet_keeps_outer_seam_as_read_only_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifests = _make_project(tmp_path, pages=11, max_words=700)
    assert [len(manifest.unit_ids) for manifest in manifests] == [5, 5, 1]
    for manifest in manifests:
        _submit(root, manifest.batch_id)
    middle = manifests[1]
    units_path = root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    units[3] = units[3].model_copy(update={"continued_to_next": True})
    units[4] = units[4].model_copy(update={"continues_from_previous": True})
    write_jsonl(units_path, units)
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
    snapshot = batch_unit_fingerprints(root, middle.batch_id)
    base = ExternalReviewRun(
        run_id="outer-seam-base",
        batch_id=middle.batch_id,
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
        source_fingerprint=batch_source_fingerprint(root, middle.batch_id),
        structure_fingerprint=batch_structure_fingerprint(root, middle.batch_id),
        context_fingerprint=external_review._external_review_context_fingerprint(
            root,
            middle.batch_id,
            list(snapshot),
            ReviewScope.FULL,
        ),
    )
    append_jsonl(
        root / "reviews" / f"{middle.batch_id}.external-runs.jsonl", [base]
    )
    current = translation_map(root)
    first_id = middle.translatable_unit_ids[0]
    current[first_id] = current[first_id].model_copy(
        update={"target_text": "批次边界修订", "revision": 2}
    )
    write_jsonl(root / "translations" / "current.jsonl", current.values())

    scope, _, covered, _ = _primary_review_scope(root, middle.batch_id, None)
    assert scope is ReviewScope.INCREMENTAL
    outside_ids = manifests[0].unit_ids[-2:]
    context_ids = external_review._outer_seam_context_ids(
        root, middle.batch_id, covered
    )
    assert context_ids == outside_ids
    assert not set(outside_ids) & set(covered)
    packet, pages = external_review._packet_text(
        root,
        middle.batch_id,
        covered,
        read_only_context_ids=context_ids,
    )
    assert all(
        f"## Unit {unit_id} [READ-ONLY SEAM CONTEXT]" in packet
        for unit_id in outside_ids
    )
    assert "do not report issues against them" in packet
    assert pages == [4, 5, 6, 7]

    _audit_and_approve(root, middle.batch_id)
    captured_evidence: dict[str, tuple[str, str]] = {}

    def invoke(
        reviewer: ExternalReviewerConfig,
        packet_path: Path,
        work_dir: Path,
        evidence: dict[str, tuple[str, str]],
        **kwargs: object,
    ) -> tuple[object, ...]:
        captured_evidence.update(evidence)
        return (
            {"verdict": "accepted", "summary": "No defects found.", "issues": []},
            "{}",
            reviewer.model,
            reviewer.effort,
            reviewer.model,
            "off",
            1,
            PromptDelivery.FILE,
            1.0,
            ReviewUsage(input_tokens=100, provider_turns=2),
            0.01,
        )

    monkeypatch.setattr(external_review, "_invoke", invoke)
    monkeypatch.setattr(
        external_review, "_command_version", lambda command: "test"
    )
    result = external_review.run_external_review(root, middle.batch_id)

    assert result["external_approvable"]
    assert set(captured_evidence) == set(covered)
    assert not set(outside_ids) & set(captured_evidence)

    accepted = read_jsonl(
        root / "reviews" / f"{middle.batch_id}.external-runs.jsonl",
        ExternalReviewRun,
    )[-1]
    current = translation_map(root)
    outside_id = outside_ids[-1]
    current[outside_id] = current[outside_id].model_copy(
        update={"target_text": "批外接缝译文已变更", "revision": 2}
    )
    write_jsonl(root / "translations" / "current.jsonl", current.values())

    assert not external_review_status(root, middle.batch_id)["external_approvable"]
    assert accepted.context_fingerprint != (
        external_review._external_review_context_fingerprint(
            root,
            middle.batch_id,
            accepted.covered_unit_ids,
            accepted.scope,
        )
    )


def test_external_status_does_not_reuse_an_old_second_opinion(
    tmp_path: Path,
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    _submit(root, batch_id)
    config = load_project(root)
    config.external_review = ExternalReviewConfig(
        reviewers=[
            ExternalReviewerConfig(
                id="claude",
                driver="claude-code",
                command="claude",
                model="claude-sonnet-5",
                fast=False,
            )
        ]
    )
    save_project(root, config)
    fingerprint = external_review.batch_translation_fingerprint(root, batch_id)
    suggestion = ReviewIssue(
        issue_id="low-confidence-suggestion",
        batch_id=batch_id,
        unit_id=manifests[0].unit_ids[0],
        severity=Severity.SUGGESTION,
        type=IssueType.STYLE,
        explanation="A low-confidence point requires an independent second opinion.",
        confidence=0.2,
        reviewer="external:claude",
    )
    write_jsonl(root / "reviews" / f"{batch_id}.issues.jsonl", [suggestion])
    context_fingerprint = external_review._external_review_context_fingerprint(
        root,
        batch_id,
        list(manifests[0].unit_ids),
        ReviewScope.FULL,
    )

    def run(
        run_id: str,
        role: str,
        *,
        base_run_id: str | None = None,
        issue_ids: list[str] | None = None,
    ) -> ExternalReviewRun:
        return ExternalReviewRun(
            run_id=run_id,
            batch_id=batch_id,
            reviewer_id="claude",
            driver="claude-code",
            role=role,
            requested_model="claude-sonnet-5",
            actual_model="claude-sonnet-5",
            model_verified=True,
            translation_fingerprint=fingerprint,
            packet_sha256="0" * 64,
            prompt_version="test",
            base_run_id=base_run_id,
            verdict=ExternalReviewVerdict.ACCEPTED,
            summary="No substantive defects found.",
            issue_ids=issue_ids or [],
            context_fingerprint=context_fingerprint,
        )

    append_jsonl(
        root / "reviews" / f"{batch_id}.external-runs.jsonl",
        [
            run("primary-one", "primary"),
            run("second-for-one", "second-opinion", base_run_id="primary-one"),
            run(
                "primary-two",
                "primary",
                issue_ids=[suggestion.issue_id],
            ),
        ],
    )

    status = external_review_status(root, batch_id)

    assert status["second_opinion_required"] is True
    assert status["second_opinion"] is None
    assert status["verdict"] == ExternalReviewVerdict.INCONCLUSIVE
    assert status["external_approvable"] is False


@pytest.mark.parametrize("schema_version", [1, 2])
def test_v4_migration_rejects_pre_v3_source_schemas(
    tmp_path: Path, schema_version: int
) -> None:
    root, _ = _make_project(tmp_path, 1)
    config = load_project(root)
    config.schema_version = schema_version
    save_project(root, config)
    project_before = (root / "project.yaml").read_bytes()

    with pytest.raises(
        ValueError,
        match=f"requires a schema-v3 source project.*schema {schema_version}",
    ):
        migrate_project_schema(root, 4)

    assert (root / "project.yaml").read_bytes() == project_before
    assert not (root / "evidence" / "migration-v3-v4.json").exists()


def test_v3_migration_preserves_bytes_and_only_certifies_bound_evidence(
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
    write_yaml(
        root / "glossary" / "approved.yaml",
        {
            "terms": [
                {"source": "architecture", "target": "架构"},
                {
                    "source": "quantum chromodynamics",
                    "target": "量子色动力学",
                },
            ]
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
    legacy_packet = external_review._legacy_v3_packet_text(root, batch_id)[0]
    compact_packet = external_review._packet_text(root, batch_id)[0]
    assert "quantum chromodynamics" in legacy_packet
    assert "quantum chromodynamics" not in compact_packet
    legacy_packet_sha256 = sha256_text(legacy_packet)
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
                packet_sha256=legacy_packet_sha256,
                prompt_version="v3",
                verdict=ExternalReviewVerdict.ACCEPTED,
                summary="Accepted under the v3 evidence contract.",
            ),
            ExternalReviewRun(
                schema_version=1,
                run_id="legacy-second-opinion",
                batch_id=batch_id,
                reviewer_id="legacy-second-reviewer",
                driver="antigravity",
                role="second-opinion",
                requested_model="legacy-second-model",
                actual_model="legacy-second-model",
                model_verified=True,
                translation_fingerprint=legacy_fingerprint,
                packet_sha256=legacy_packet_sha256,
                prompt_version="v3",
                verdict=ExternalReviewVerdict.ACCEPTED,
                summary="Second opinion accepted under the v3 evidence contract.",
            ),
        ],
    )
    current_before = (root / "translations" / "current.jsonl").read_bytes()
    history_before = (root / "translations" / "history.jsonl").read_bytes()
    preview = migrate_project_schema(root, 4, dry_run=True)
    assert preview["changed"] is False
    assert preview["importable"] == {
        "qa": 0,
        "audit_lenses": 0,
        "external_runs": 2,
    }
    assert preview["pending_recheck"] == {
        "qa": [batch_id],
        "audit": [batch_id],
        "external": [],
    }
    report = migrate_project_schema(root, 4)
    assert report["source_verification"]["passed"]
    assert load_project(root).schema_version == 4
    assert (root / "translations" / "current.jsonl").read_bytes() == current_before
    assert (root / "translations" / "history.jsonl").read_bytes() == history_before
    migrated_qa = read_json(qa_path)
    assert "qa_context_fingerprint" not in migrated_qa
    assert workflow_next(root)["stage"] == "qa"
    rerun = run_qa(root, batch_id)
    assert not rerun.passed
    assert {item.code for item in rerun.errors} == {"approved-term-missing"}
    assert not audit_coverage(root, batch_id)["complete"]
    migrated_runs = read_jsonl(runs_path, ExternalReviewRun)
    migrated = next(run for run in migrated_runs if run.run_id == "legacy-run-v4")
    assert migrated.schema_version == 2
    assert migrated.base_run_id == "legacy-run"
    migrated_second = next(
        run
        for run in migrated_runs
        if run.run_id == "legacy-second-opinion-v4"
    )
    assert migrated_second.base_run_id == migrated.run_id


@pytest.mark.parametrize(
    "record_labels",
    [
        [],
        [FigureLabel(source="Open", target="开启")],
    ],
    ids=["empty-record-labels", "partial-record-labels"],
)
def test_v3_migration_reconstructs_legacy_figure_label_packets(
    tmp_path: Path,
    record_labels: list[FigureLabel],
) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    units_path = root / "derived" / "units.jsonl"
    original = read_jsonl(units_path, SourceUnit)[0]
    unit = original.model_copy(
        update={
            "kind": UnitKind.FIGURE,
            "figure_labels": [
                FigureLabel(source="Open", target="打开"),
                FigureLabel(source="Close", target="关闭"),
            ],
            "visual_text_status": SemanticStatus.VERIFIED,
        }
    )
    write_jsonl(units_path, [unit])
    assert verify_extraction(root, "all", force=True)["passed"]
    refresh_batch(root, batch_id)
    record = TranslationRecord(
        unit_id=unit.unit_id,
        target_text="控件状态图。",
        figure_labels=record_labels,
        source_hash=unit.source_hash,
    )
    write_jsonl(root / "translations" / "current.jsonl", [record])
    legacy_fingerprint = _legacy_v3_batch_fingerprint(root, batch_id)
    legacy_packet = external_review._legacy_v3_packet_text(root, batch_id)[0]
    assert "Figure label sources:" in legacy_packet
    assert ("Figure label sources:\n- Open" in legacy_packet) is bool(
        record_labels
    )
    assert "- Close" not in legacy_packet
    if record_labels:
        with pytest.raises(ValueError, match="Figure label mapping mismatch"):
            external_review._packet_text(root, batch_id, compact=False)
    else:
        current_packet = external_review._packet_text(
            root, batch_id, compact=False
        )[0]
        assert "Figure label sources:\n- Open\n- Close" in current_packet

    config = load_project(root)
    config.schema_version = 3
    save_project(root, config)
    runs_path = root / "reviews" / f"{batch_id}.external-runs.jsonl"
    append_jsonl(
        runs_path,
        [
            ExternalReviewRun(
                schema_version=1,
                run_id="legacy-figure-run",
                batch_id=batch_id,
                reviewer_id="legacy-reviewer",
                driver="claude-code",
                role="primary",
                requested_model="legacy-model",
                actual_model="legacy-model",
                model_verified=True,
                translation_fingerprint=legacy_fingerprint,
                packet_sha256=sha256_text(legacy_packet),
                prompt_version="v3",
                verdict=ExternalReviewVerdict.ACCEPTED,
                summary="Accepted under the v3 figure-label contract.",
            )
        ],
    )

    report = migrate_project_schema(root, 4)

    assert report["importable"]["external_runs"] == 1
    assert report["pending_recheck"]["external"] == []
    migrated = read_jsonl(runs_path, ExternalReviewRun)[-1]
    assert migrated.run_id == "legacy-figure-run-v4"
    assert migrated.context_fingerprint


def test_v3_migration_reconstructs_legacy_equation_packets(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    units_path = root / "derived" / "units.jsonl"
    original = read_jsonl(units_path, SourceUnit)[0]
    unit = original.model_copy(
        update={
            "kind": UnitKind.EQUATION,
            "translatable": False,
            "latex": r"E = mc^2",
            "equation_number": "7.3",
            "math_status": SemanticStatus.VERIFIED,
        }
    )
    write_jsonl(units_path, [unit])
    refresh_batch(root, batch_id)
    legacy_fingerprint = _legacy_v3_batch_fingerprint(root, batch_id)
    legacy_packet = external_review._legacy_v3_packet_text(root, batch_id)[0]
    current_packet = external_review._packet_text(root, batch_id, compact=False)[0]
    assert original.source_text in legacy_packet
    assert r"E = mc^2 \tag{7.3}" not in legacy_packet
    assert r"E = mc^2 \tag{7.3}" in current_packet

    config = load_project(root)
    config.schema_version = 3
    save_project(root, config)
    runs_path = root / "reviews" / f"{batch_id}.external-runs.jsonl"
    append_jsonl(
        runs_path,
        [
            ExternalReviewRun(
                schema_version=1,
                run_id="legacy-equation-run",
                batch_id=batch_id,
                reviewer_id="legacy-reviewer",
                driver="claude-code",
                role="primary",
                requested_model="legacy-model",
                actual_model="legacy-model",
                model_verified=True,
                translation_fingerprint=legacy_fingerprint,
                packet_sha256=sha256_text(legacy_packet),
                prompt_version="v3",
                verdict=ExternalReviewVerdict.ACCEPTED,
                summary="Accepted under the v3 equation contract.",
            )
        ],
    )

    report = migrate_project_schema(root, 4)

    assert report["importable"]["external_runs"] == 1
    assert report["pending_recheck"]["external"] == []
    migrated = read_jsonl(runs_path, ExternalReviewRun)[-1]
    assert migrated.run_id == "legacy-equation-run-v4"
    assert migrated.context_fingerprint


def test_v3_migration_does_not_resurrect_superseded_external_acceptance() -> None:
    fingerprint = "legacy-fingerprint"
    accepted = ExternalReviewRun(
        schema_version=1,
        run_id="old-accepted",
        batch_id="legacy-batch",
        reviewer_id="legacy-reviewer",
        driver="claude-code",
        role="primary",
        requested_model="legacy-model",
        actual_model="legacy-model",
        model_verified=True,
        translation_fingerprint=fingerprint,
        packet_sha256="0" * 64,
        prompt_version="v3",
        verdict=ExternalReviewVerdict.ACCEPTED,
        summary="Older accepted review.",
    )
    failed = accepted.model_copy(
        update={
            "run_id": "newer-failed",
            "model_verified": False,
            "verdict": ExternalReviewVerdict.INCONCLUSIVE,
            "summary": "Newer review could not verify the configured model.",
            "success": False,
        }
    )

    chain, pending_recheck = _migratable_v3_external_chain(
        [accepted, failed], fingerprint
    )

    assert chain == []
    assert pending_recheck is True


def test_v3_migration_keeps_schema_retryable_when_verification_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _make_project(tmp_path, 1)
    config = load_project(root)
    config.schema_version = 3
    save_project(root, config)
    import littrans.verification as verification

    original_verify = verification.verify_extraction

    def fail_verification(*args: object, **kwargs: object) -> dict[str, object]:
        raise ValueError("forced migration verification failure")

    monkeypatch.setattr(verification, "verify_extraction", fail_verification)
    with pytest.raises(ValueError, match="forced migration verification failure"):
        migrate_project_schema(root, 4)

    assert load_project(root).schema_version == 3
    assert not (root / "evidence" / "migration-v3-v4.json").exists()

    monkeypatch.setattr(verification, "verify_extraction", original_verify)
    report = migrate_project_schema(root, 4)
    assert report["source_verification"]["passed"]
    assert load_project(root).schema_version == 4


def test_exact_three_batch_render_runs_seam_qa(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path)
    for manifest in manifests:
        _submit(root, manifest.batch_id)
    for manifest in manifests:
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
