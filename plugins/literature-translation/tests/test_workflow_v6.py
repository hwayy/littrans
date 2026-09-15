"""Production entry points, exercised against an explicitly generated source oracle."""
from pathlib import Path

import pymupdf as fitz
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
    prepare_source(root, allow_missing_layout=True)
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


def test_optional_assets_and_reviewed_translation_with_untranscribed_assets(project: Path) -> None:
    bid = "sample-one-b001"
    ready = workflow_next(project, host="codex")
    assert ready["stage"] == "translate"
    assert {task["stage"] for task in ready["ready_tasks"]} == {"translate"}
    assert {task["stage"] for task in ready["optional_asset_tasks"]} == {"transcribe"}
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
                  target_text=u.source_text.replace("The identity holds.", "\u8be5\u6052\u7b49\u5f0f\u6210\u7acb\u3002"), image_evidence=original_context(project, [u])["required_images"])
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
    assert state["complete"]
    assert not state["assets_complete"]
    assert not state["ready_tasks"]
    completed = workflow_next(project, start_at=bid, through=bid)
    assert completed["stage"] == "complete"
    assert completed["ready_tasks"] == []
    assert {task["stage"] for task in completed["optional_asset_tasks"]} == {"transcribe"}
    assert create_workflow_packet(project, "transcribe", [bid])["asset_ids"]
    assert representation_status(project)["counts"]["transcribe"] > 0
    # No candidate exists anywhere in the project, so the edition is originals-only.
    output = render_project(project, None, batch_id=bid)
    assert output["originals_only_reason"] == "no-transcription-candidates"
    rendered = Path(output["html"]).read_text(encoding="utf-8")
    assert "转写未完成" not in rendered
    assert "该恒等式成立" in rendered
    assert 'data-original-only="true"' in rendered
    assert "tex2svgPromise" not in rendered
    assert "https://cdn" not in rendered
    auto_qa = read_json(Path(output["render_qa"]))
    assert auto_qa["selection"]["originals_only"] is True
    assert auto_qa["selection"]["originals_only_reason"] == "no-transcription-candidates"
    assert auto_qa["rendered_status"] == "machine-reviewed"
    originals = render_project(project, None, name="originals-only", batch_id=bid, originals_only=True)
    original_html = Path(originals["html"]).read_text(encoding="utf-8")
    assert "asset-candidate" not in original_html.replace(".asset-candidate", "")
    assert "tex2svgPromise" not in original_html
    assert originals["originals_only_reason"] == "requested"
    assert read_json(Path(originals["render_qa"]))["selection"] == {
        **auto_qa["selection"], "originals_only_reason": "requested",
    }

    # One candidate anywhere restores the labelled candidate path.
    from littrans.representations import submit_candidates

    write_json(project / "candidate.json", {
        "packet_id": transcription["packet_id"], "author_task_id": "test-transcriber",
        "model": "gpt-5.6-luna", "reasoning_effort": "max",
        "image_evidence": transcription["required_images"], "usage": None,
        "candidates": [{"asset_id": aid, "format": "latex", "content": "1+1=2"}
                       for aid in transcription["asset_ids"]],
    })
    submit_candidates(project, project / "candidate.json")
    labelled = render_project(project, None, name="with-candidate", batch_id=bid)
    assert "originals_only_reason" not in labelled
    labelled_html = Path(labelled["html"]).read_text(encoding="utf-8")
    assert "转写未完成" in labelled_html
    assert "asset-original" in labelled_html
    assert read_json(Path(labelled["render_qa"]))["selection"]["originals_only_reason"] is None


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
    assert {task["stage"] for task in state["ready_tasks"]} == {"translate"}
    assert {task["stage"] for task in state["optional_asset_tasks"]} == {"asset-audit"}


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
                   target_text=u.source_text.replace("The identity holds.", "\u8be5\u6052\u7b49\u5f0f\u6210\u7acb\u3002"), image_evidence=original_context(project, [u])["required_images"],
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


def test_nontranslatable_asset_ignores_historic_translation(project):
    from littrans.workflow import _audit_unit_text
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    unit = next(u for u in units if "{{asset:" in u.source_text)
    # Synthetic source-only formula can retain a historical record after rebuilding.
    unit.translatable = False
    old = TranslationRecord(unit_id=unit.unit_id, source_hash="obsolete", target_text="{{asset:removed-original}}")
    assert "removed-original" not in _audit_unit_text(unit, old)
    assert "[source-only]" in _audit_unit_text(unit, old)
    # Test the production render path against a now-nontranslatable selected unit.
    for current in units:
        if current.unit_id == unit.unit_id:
            current.translatable = False
    write_jsonl(project / "derived/units.jsonl", units)
    write_jsonl(project / "translations/current.jsonl", [old])
    output = render_project(project, "1", "historic-formula-draft", allow_draft=True, originals_only=True)
    assert all("removed-original" not in Path(path).read_text(encoding="utf-8")
               for key, path in output.items() if key in {"markdown", "html"})


@pytest.mark.parametrize("same_source_hash", [False, True])
def test_unchanged_translation_retains_fresh_images_after_boundary_repair(tmp_path, monkeypatch, same_source_hash):
    """A real glyph ownership repair changes the image without changing Chinese."""
    import littrans.fidelity as fidelity
    from littrans.context_packets import validate_translation_images

    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {
        "status": "unavailable", "reason": "Synthetic boundary oracle", "pages": {},
    })
    source = tmp_path / "boundary.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        # A balanced parenthesis around the notation stays in the automatic candidate
        # (an unbalanced prose one is trimmed by balance), so the reviewer's decision
        # to drop it is a genuine glyph ownership repair.
        page.insert_text((50, 60), "Let x = (1) be the size.")
        doc.save(source)
    root = tmp_path / "boundary-project"
    initialize_project(source, root, "technical-book")
    prepare_source(root, allow_missing_layout=True)

    def review_source(override=None):
        packet = build_source_review_packet(root)
        review = read_json(Path(packet["review_template"]))
        review["reviewer"] = "generated-boundary-oracle"
        for decision in review["pages"]:
            for key in ("viewed_original", "coverage_complete", "boundaries_complete",
                        "reading_order_correct", "grouping_checked", "layout_fallback_checked"):
                decision[key] = True
            if override:
                decision["override"] = override
        write_json(root / "boundary-review.json", review)
        return import_source_review(root, root / "boundary-review.json", True)

    review_source()
    create_batches(root, "1", prefix="boundary")
    bid = "boundary-b001"
    units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    old_records = [TranslationRecord(unit_id=u.unit_id, source_hash=u.source_hash,
                   target_text=u.source_text.replace("Let", "Suppose"),
                   image_evidence=original_context(root, [u])["required_images"])
                   for u in units if u.translatable]
    write_jsonl(root / "input.jsonl", old_records)
    submit_translation(root, bid, root / "input.jsonl")
    history = (root / "translations/history.jsonl").read_bytes()
    packet = build_source_review_packet(root)
    page = read_json(Path(packet["packet_path"]))["pages"][0]
    glyphs = {g["id"]: g for g in page["ledger"]["glyphs"]}
    regions = []
    removed = []
    for asset in page["assets"]:
        fragment = asset["fragments"][0]
        ids = fragment["glyph_ids"]
        removed.extend(gid for gid in ids if glyphs[gid]["text"] == ")")
        regions.append({"id": asset["id"], "kind": asset["kind"], "bbox": fragment["bbox"],
                        "glyph_ids": [gid for gid in ids if glyphs[gid]["text"] != ")"],
                        "display": asset["display"], "grouping_pending": False})
    assert removed
    assert review_source({"regions": regions})["requires_new_packet"]
    review_source()
    units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    old_by_id = {record.unit_id: record for record in old_records}
    fresh = [old_by_id[u.unit_id].model_copy(update={"source_hash": u.source_hash,
             "image_evidence": original_context(root, [u])["required_images"]})
             for u in units if u.translatable]
    assert any(old_by_id[r.unit_id].image_evidence != r.image_evidence for r in fresh)
    assert any(old_by_id[r.unit_id].source_hash != r.source_hash for r in fresh)
    if same_source_hash:
        # Reproduce a prior rebind that already refreshed the source hash but
        # accidentally retained the obsolete receipt, as in the reported bug.
        write_jsonl(root / "translations/current.jsonl", [
            old_by_id[r.unit_id].model_copy(update={"source_hash": r.source_hash}) for r in fresh])
    write_jsonl(root / "input.jsonl", fresh)
    returned = submit_translation(root, bid, root / "input.jsonl")
    current = read_jsonl(root / "translations/current.jsonl", TranslationRecord)
    batch = read_jsonl(root / "batches" / bid / "translation.jsonl", TranslationRecord)
    assert returned == current == batch
    assert (root / "translations/history.jsonl").read_bytes() == history
    for record in current:
        assert record.revision == old_by_id[record.unit_id].revision
        unit = next(u for u in units if u.unit_id == record.unit_id)
        validate_translation_images(root, unit, record.image_evidence)
        assert record.image_evidence == original_context(root, [unit])["required_images"]
