import hashlib
import subprocess
from pathlib import Path

import pymupdf as fitz
import pytest
from test_asset_representation import candidate_input, review_input
from test_asset_representation import project as asset_project
from test_efficiency_v4 import _submit
from test_fidelity_source import approve
from test_footnotes_v6 import _linked_notes
from test_workflow_v6 import project as workflow_project

from littrans import fidelity, layout_detector, representations
from littrans.batching import create_batches
from littrans.fidelity_models import load_assets
from littrans.models import AssetTranslation, FigureLabel, SourceUnit, TableData, TranslationRecord
from littrans.quality import current_qa_context_fingerprint, run_qa
from littrans.storage import read_json, read_jsonl, write_json, write_jsonl
from littrans.workflow import create_workflow_packet

asset_root = asset_project
project = workflow_project


@pytest.mark.parametrize("change", ["missing", "record-hash", "source-hash", "text"])
def test_dependency_changes_invalidate_cached_qa(tmp_path, change):
    root, batches, units = _linked_notes(tmp_path)
    _submit(root, batches[0].batch_id, target_text="译文[^1]")
    _submit(root, batches[1].batch_id, target_text="注释")
    assert run_qa(root, batches[0].batch_id).passed
    before = current_qa_context_fingerprint(root, batches[0].batch_id)
    records = read_jsonl(root / "translations/current.jsonl", TranslationRecord)
    note = next(r for r in records if r.unit_id == units[1].unit_id)
    if change == "missing":
        records.remove(note)
    elif change == "record-hash":
        note.source_hash = "stale"
    elif change == "text":
        note.target_text = "修改后的注释"
    else:
        units[1].source_hash = "new-source"
        write_jsonl(root / "derived/units.jsonl", units)
        approve(root, "all")
    write_jsonl(root / "translations/current.jsonl", records)
    assert current_qa_context_fingerprint(root, batches[0].batch_id) != before
    if change != "text":
        assert not run_qa(root, batches[0].batch_id).passed


@pytest.mark.parametrize("mixed", [False, True])
def test_creation_keeps_stale_records_editable(project, mixed):
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    unit = next(u for u in units if u.page == 1 and u.translatable)
    write_jsonl(project / "translations/current.jsonl", [TranslationRecord(unit_id=unit.unit_id, source_hash="old", target_text="过期译文")])
    if mixed:
        unit.parent_id = "group"
        sibling = unit.model_copy(deep=True, update={"unit_id": "pending-sibling", "source_text": "Sibling",
            "source_markdown": "Sibling", "asset_refs": [], "asset_content_hashes": {}})
        units.insert(units.index(unit) + 1, sibling)
        write_jsonl(project / "derived/units.jsonl", units)
        approve(project, "all")
    batch = create_batches(project, "1", prefix="stale-create", untranslated_only=True)[0]
    assert unit.unit_id in batch.translatable_unit_ids
    assert unit.unit_id not in batch.read_only_unit_ids


def fail_index(monkeypatch, root):
    original = representations.write_json
    def write(path, value):
        if path == root / "evidence/representations/index.json":
            raise KeyboardInterrupt
        original(path, value)
    monkeypatch.setattr(representations, "write_json", write)
    return original


def test_candidate_replay_replaces_stale_index(asset_root, monkeypatch):
    candidate_input(asset_root)
    representations.submit_candidates(asset_root, asset_root / "candidate.json")
    assets = load_assets(asset_root)
    assets["a1"].content_sha256 = hashlib.sha256(b"changed-source").hexdigest()
    write_jsonl(asset_root / "derived/fidelity-assets.jsonl", assets.values())
    candidate_input(asset_root, "x=2")
    original = fail_index(monkeypatch, asset_root)
    with pytest.raises(KeyboardInterrupt):
        representations.submit_candidates(asset_root, asset_root / "candidate.json")
    monkeypatch.setattr(representations, "write_json", original)
    assert representations.submit_candidates(asset_root, asset_root / "candidate.json")["replayed"]
    assert representations.representation_status(asset_root, ["a1"])["assets"]["a1"]["state"] == "asset-audit"


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_review_replay_replaces_invalid_mapping(asset_root, monkeypatch, damage):
    candidate_input(asset_root)
    representations.submit_candidates(asset_root, asset_root / "candidate.json")
    old = review_input(asset_root)
    representations.import_asset_review(asset_root, asset_root / "review.json", True)
    path = asset_root / "evidence/representations/reviews" / f"{old['packet_id']}.json"
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("{}", encoding="utf-8")
    fresh = review_input(asset_root)
    assert fresh["packet_id"] != old["packet_id"]
    original = fail_index(monkeypatch, asset_root)
    with pytest.raises(KeyboardInterrupt):
        representations.import_asset_review(asset_root, asset_root / "review.json", True)
    monkeypatch.setattr(representations, "write_json", original)
    assert representations.import_asset_review(asset_root, asset_root / "review.json", True)["replayed"]
    assert representations.representation_status(asset_root, ["a1"])["assets"]["a1"]["state"] == "verified"


def test_failed_canvas_encoding_preserves_existing_page(project, monkeypatch):
    canvas = project / "evidence/pages/fidelity-p0001.png"
    before = canvas.read_bytes()
    class BrokenPixmap:
        def save(self, path):
            Path(path).write_bytes(b"partial")
            raise KeyboardInterrupt
        def tobytes(self, *args):
            raise KeyboardInterrupt
    monkeypatch.setattr(fitz.Page, "get_pixmap", lambda *args, **kwargs: BrokenPixmap())
    with pytest.raises(KeyboardInterrupt):
        fidelity.prepare_source(project, "1", replace=True, allow_missing_layout=True)
    assert canvas.read_bytes() == before


@pytest.mark.parametrize("field", ["text", "table", "label-source", "label-target"])
@pytest.mark.parametrize("reference", ["{{asset:a1}}", "{{asset:unknown}}", "{{asset:broken"])
def test_companions_reject_asset_placeholders(asset_root, field, reference):
    item = AssetTranslation(asset_id="a1", target_text="中文说明")
    if field == "text":
        item.target_text += reference
    elif field == "table":
        item.target_table = TableData(rows=[["中文" + reference]], column_count=1)
    else:
        item.figure_labels = [FigureLabel(source=reference if field == "label-source" else "Label",
                                         target="中文" + (reference if field == "label-target" else ""))]
    errors = representations.validate_asset_translations(asset_root, "{{asset:a1}}", [item])
    assert any(e["code"] == "asset-reference-in-companion" for e in errors)


@pytest.mark.parametrize("invalid", [None, {}, "bad"])
@pytest.mark.parametrize("where", ["cache", "worker"])
def test_detector_checks_prediction_lists(tmp_path, monkeypatch, invalid, where):
    python = tmp_path / "python.exe"
    python.touch()
    model = tmp_path / "model"
    model.mkdir()
    image = tmp_path / "page.png"
    image.touch()
    monkeypatch.setattr(layout_detector, "runtime_paths", lambda: (python, model))
    monkeypatch.setattr(layout_detector, "_runtime_identity", lambda _: {"python": "test"})
    store = tmp_path / "layout"
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        request = read_json(Path(command[2]))
        write_json(Path(command[3]), {"status": "ok", "fingerprint": request["fingerprint"],
                                      "pages": {str(image.resolve()): invalid if where == "worker" else []}})
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(layout_detector.subprocess, "run", run)
    result = layout_detector.detect_layout([image], store)
    output = Path(result["path"])
    assert output.parent == store and (result["status"] != "ok" or output.stem == result["fingerprint"])
    if where == "worker":
        assert result["status"] == "unavailable"
    else:
        # Published results are keyed by image content, not by the request's paths.
        key = hashlib.sha256(image.read_bytes()).hexdigest()
        assert set(read_json(output)["pages"]) == {key} and read_json(output)["images"] == {str(image.resolve()): key}
        damaged = read_json(output)
        damaged["pages"][key] = invalid
        write_json(output, damaged)
        assert layout_detector.detect_layout([image], store)["pages"][key] == []
        assert len(calls) == 2


@pytest.mark.parametrize("mixed", [False, True])
def test_damaged_recovery_packet_excludes_fresh_assets(project, mixed):
    batch = create_batches(project, "all", prefix="recovery-scope")[0]
    assets = list(load_assets(project))
    packet = representations.build_asset_packet(project, [assets[0]])
    write_json(project / "candidate.json", {"packet_id": packet["packet_id"], "author_task_id": "writer",
        "model": packet["model"], "reasoning_effort": packet["reasoning_effort"], "image_evidence": packet["required_images"],
        "candidates": [{"asset_id": assets[0], "format": "latex", "content": "1+1=2"}]})
    result = representations.submit_candidates(project, project / "candidate.json")
    if mixed:
        second = representations.build_asset_packet(project, [assets[1]])
        payload = read_json(project / "candidate.json")
        payload.update(packet_id=second["packet_id"], image_evidence=second["required_images"])
        payload["candidates"][0]["asset_id"] = assets[1]
        write_json(project / "second.json", payload)
        representations.submit_candidates(project, project / "second.json")
        audit = representations.build_asset_packet(project, [assets[1]], "asset-audit")
        write_json(project / "second-review.json", {"packet_id": audit["packet_id"], "reviewer_task_id": "reviewer",
            "render_artifact_sha256": audit["render_artifact"]["sha256"], "render_manifest_sha256": audit["render_manifest_sha256"],
            "image_evidence": audit["required_images"], "decisions": [{"asset_id": assets[1],
                "candidate_sha256": audit["candidates"][assets[1]]["candidate_sha256"], "verdict": "reject",
                "visual_checked": True, "render_checked": True, "semantic_uncertainty": "Meaning unclear"}]})
        representations.import_asset_review(project, project / "second-review.json", True)
    (project / "evidence/representations/candidates" / f"{result['candidate_sha256'][assets[0]]}.json").unlink()
    recovery = create_workflow_packet(project, "transcribe", [batch.batch_id])
    assert recovery["asset_ids"] == (assets if mixed else [assets[0]])
    assert set(recovery["revision_context"]) == ({assets[1]} if mixed else set())
    write_json(project / "recovered.json", {"packet_id": recovery["packet_id"], "author_task_id": "writer",
        "model": recovery["model"], "reasoning_effort": recovery["reasoning_effort"], "image_evidence": recovery["required_images"],
        "candidates": [{"asset_id": aid, "format": "latex", "content": "1+1=2"} for aid in recovery["asset_ids"]]})
    representations.submit_candidates(project, project / "recovered.json")


def test_canvas_rolls_back_after_later_publication_failure(project, monkeypatch):
    canvas = project / "evidence/pages/fidelity-p0001.png"
    before = canvas.read_bytes()
    original_bytes = fidelity.atomic_write_bytes
    original_json = fidelity.write_json
    def altered(path, content):
        original_bytes(path, content + b"engine-change" if path == canvas else content)
    def fail(path, payload):
        original_json(path, payload)
        if path == project / "derived/fidelity-pages/p0001.json":
            raise KeyboardInterrupt
    monkeypatch.setattr(fidelity, "atomic_write_bytes", altered)
    monkeypatch.setattr(fidelity, "write_json", fail)
    with pytest.raises(KeyboardInterrupt):
        fidelity.prepare_source(project, "1", replace=True, allow_missing_layout=True)
    assert canvas.read_bytes() == before
    assert fidelity.verify_fidelity(project)["passed"]
