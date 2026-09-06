"""Production entry points, exercised against an explicitly generated source oracle."""
from pathlib import Path

import fitz
import pytest
from typer.testing import CliRunner

from littrans.batching import create_batches
from littrans.cli import app
from littrans.context_packets import original_context
from littrans.fidelity import build_source_review_packet, import_source_review, prepare_source
from littrans.models import SourceUnit, TranslationRecord, WorkflowPacketManifest
from littrans.project import initialize_project
from littrans.quality import approve_batch, run_qa
from littrans.rendering import render_project
from littrans.representations import representation_status
from littrans.storage import read_json, read_jsonl, write_json, write_jsonl
from littrans.translation import submit_translation
from littrans.workflow import (
    create_workflow_packet,
    import_review_set,
    workflow_next,
    workflow_status,
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import littrans.fidelity as fidelity
    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {
        "status": "unavailable", "reason": "Generated-source oracle", "pages": {},
    })
    source = tmp_path / "oracle.pdf"
    with fitz.open() as doc:
        for _ in range(2):
            page = doc.new_page()
            page.insert_text((50, 60), "The identity holds.")
            page.insert_text((50, 100), "1+1=2")
        doc.save(source)
    root = tmp_path / "project"
    initialize_project(source, root, "technical-book")
    prepare_source(root)
    packet = build_source_review_packet(root)
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "generated-pdf-test-oracle"
    payload = read_json(Path(packet["packet_path"]))
    for page in payload["pages"]:
        assert page["assets"]
        assert all(a["kind"] == "math" for a in page["assets"])
    for decision in review["pages"]:
        for key in ("viewed_original", "coverage_complete", "boundaries_complete",
                    "reading_order_correct", "grouping_checked", "layout_fallback_checked"):
            decision[key] = True
        decision["notes"] = "Synthetic oracle: known prose and 1+1=2, no production approval."
    write_json(root / "oracle-review.json", review)
    import_source_review(root, root / "oracle-review.json", True)
    create_batches(root, "1", prefix="sample-one")
    create_batches(root, "2", prefix="sample-two")
    return root


def test_parallel_jobs_and_reviewed_translation_with_untranscribed_assets(project: Path) -> None:
    bid = "sample-one-b001"
    ready = workflow_next(project, host="codex")
    assert ready["stage"] == "parallel"
    assert {task["stage"] for task in ready["ready_tasks"]} == {"translate", "transcribe"}
    packet = create_workflow_packet(project, "translate", [bid])
    assert isinstance(packet, WorkflowPacketManifest)
    packet_dir = project / packet.storage_root / packet.packet_id
    context = read_json(packet_dir / "original-images.json")
    assert context["candidate_access"] is False
    assert context["read_only_context"]
    assert context["model_policy"]["codex"]["translate"] == "gpt-5.6-luna"
    transcription = create_workflow_packet(project, "transcribe", [bid])
    assert isinstance(transcription, dict)
    assert transcription["asset_ids"]
    units = [u for u in read_jsonl(project / "derived/units.jsonl", SourceUnit) if u.page == 1]
    records = [TranslationRecord(unit_id=u.unit_id, source_hash=u.source_hash,
                  target_text="该恒等式成立。", image_evidence=original_context(project, [u])["required_images"])
               for u in units if u.translatable]
    write_jsonl(project / "translation-input.jsonl", records)
    submit_translation(project, bid, project / "translation-input.jsonl")
    assert run_qa(project, bid).passed
    write_jsonl(project / "empty-issues.jsonl", [])
    for lens in ("fidelity", "technical", "chinese-style"):
        audit = create_workflow_packet(project, "audit", [bid], lens)
        assert isinstance(audit, WorkflowPacketManifest)
        import_review_set(project, project / audit.storage_root / audit.packet_id / "manifest.json",
                          project / "empty-issues.jsonl")
    approve_batch(project, bid, "machine")
    state = workflow_status(project, [bid])
    assert state["reading_complete"]
    assert not state["complete"]
    assert representation_status(project)["counts"]["transcribe"] > 0
    output = render_project(project, None, batch_id=bid)
    html_path = next(Path(v) for v in output.values() if isinstance(v, str) and v.endswith(".html"))
    rendered = html_path.read_text(encoding="utf-8")
    assert "转写未完成" in rendered
    assert "该恒等式成立" in rendered
    assert "asset-original" in rendered
    assert "https://cdn" not in rendered


def test_asset_submission_does_not_leak_into_translation_packet(project: Path) -> None:
    from littrans.representations import submit_candidates
    bid = "sample-one-b001"
    before = create_workflow_packet(project, "translate", [bid])
    transcription = create_workflow_packet(project, "transcribe", [bid])
    assert isinstance(transcription, dict)
    write_json(project / "candidate.json", {
        "packet_id": transcription["packet_id"], "author_task_id": "test-transcriber",
        "model": "gpt-5.6-luna", "reasoning_effort": "max",
        "image_evidence": transcription["required_images"], "usage": None,
        "candidates": [{"asset_id": aid, "format": "latex", "content": "1+1=2"}
                       for aid in transcription["asset_ids"]],
    })
    submit_candidates(project, project / "candidate.json")
    after = create_workflow_packet(project, "translate", [bid])
    assert isinstance(before, WorkflowPacketManifest) and isinstance(after, WorkflowPacketManifest)
    assert before.packet_id == after.packet_id
    state = workflow_status(project, [bid])
    assert {task["stage"] for task in state["ready_tasks"]} == {"translate", "asset-audit"}


def test_only_unified_source_commands_are_exposed() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["source", "--help"])
    assert result.exit_code == 0
    assert "prepare" in result.stdout
    for old in ("extract", "apply-overrides", "math-candidates", "import-math-review"):
        assert runner.invoke(app, ["source", old]).exit_code != 0
        assert "No such command" in runner.invoke(app, ["source", old]).output


def test_selected_units_cannot_cut_continuations(project: Path) -> None:
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    units[0] = units[0].model_copy(update={"continued_to_next": True})
    write_jsonl(project / "derived/units.jsonl", units)
    with pytest.raises(ValueError, match="cuts a continuation"):
        create_batches(project, "1", prefix="cut", unit_ids=[units[0].unit_id])


def test_recorded_understanding_problem_blocks_only_its_reading_batch(project: Path) -> None:
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    for page, bid in ((1, "sample-one-b001"), (2, "sample-two-b001")):
        records = [TranslationRecord(unit_id=u.unit_id, source_hash=u.source_hash,
                   target_text="该恒等式成立。", image_evidence=original_context(project, [u])["required_images"],
                   uncertainties=["Cannot determine whether the condition is necessary or sufficient."] if page == 1 else [])
                   for u in units if u.page == page and u.translatable]
        write_jsonl(project / f"input-{page}.jsonl", records)
        submit_translation(project, bid, project / f"input-{page}.jsonl")
    report = run_qa(project, "sample-one-b001")
    assert not report.passed
    assert any(item.code == "translation-understanding-unresolved" for item in report.errors)
    assert run_qa(project, "sample-two-b001").passed
    with pytest.raises(ValueError, match="passing QA"):
        approve_batch(project, "sample-one-b001", "machine")
