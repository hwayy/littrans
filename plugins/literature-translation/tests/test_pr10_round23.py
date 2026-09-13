from importlib.resources import files
from pathlib import Path

import pytest
from test_fidelity_source import approve
from test_pr10_round8 import submit
from test_workflow_v6 import project as workflow_project

from littrans import rendering
from littrans.batching import create_batches, refresh_batch
from littrans.models import (
    AssetTranslation,
    ExternalReviewConfig,
    ExternalReviewerConfig,
    FigureLabel,
    SourceUnit,
    TableData,
    UnitKind,
)
from littrans.representations import submit_candidates
from littrans.storage import (
    load_project,
    read_json,
    read_jsonl,
    save_project,
    write_json,
    write_jsonl,
)
from littrans.workflow import create_workflow_packet

project = workflow_project


@pytest.mark.parametrize("sparse,insert", [(True, False), (True, True), (False, True)])
def test_explicit_batch_scope_stays_frozen_on_refresh(project, sparse, insert):
    root = project
    units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    first = units[0]
    for name in ("middle", "last"):
        units.insert(1 if name == "middle" else 2, first.model_copy(deep=True, update={"unit_id": name,
            "source_text": name, "source_markdown": name, "parent_id": None, "asset_refs": [], "asset_content_hashes": {}}))
    write_jsonl(root / "derived/units.jsonl", units)
    approve(root, "all")
    selected = [units[0].unit_id, units[2 if sparse else 1].unit_id]
    batch = create_batches(root, "1", max_words=900, prefix="explicit", unit_ids=selected)[0]
    assert batch.unit_ids == selected
    if insert:
        units.insert(1, units[0].model_copy(deep=True, update={"unit_id": "new-interior", "parent_id": None,
            "asset_refs": [], "asset_content_hashes": {}, "source_text": "New", "source_markdown": "New"}))
        write_jsonl(root / "derived/units.jsonl", units)
    assert refresh_batch(root, batch.batch_id).unit_ids == selected


@pytest.mark.parametrize("kind", ["text", "table", "labels"])
@pytest.mark.parametrize("originals_only", [False, True])
def test_footnote_companions_remain_in_definition(project, kind, originals_only):
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    note = next(u for u in units if u.page == 1 and u.translatable)
    note.kind = UnitKind.FOOTNOTE
    note.footnote_number = "1"
    write_jsonl(project / "derived/units.jsonl", units)
    approve(project, "all")
    def change(unit, record):
        item = AssetTranslation(asset_id=next(iter(unit.asset_content_hashes)))
        if kind == "text":
            item.target_text = "脚注伴随译文"
        elif kind == "table":
            item.target_table = TableData(rows=[["脚注伴随译文"]], column_count=1)
        else:
            item.figure_labels = [FigureLabel(source="Label", target="脚注伴随译文")]
        record.asset_translations = [item]
    submit(project, "sample-one-b001", change)
    output = rendering.render_project(project, None, batch_id="sample-one-b001", allow_draft=True, originals_only=originals_only)
    markdown = Path(output["markdown"]).read_text(encoding="utf-8")
    line = next(line for line in markdown.splitlines() if "脚注伴随译文" in line)
    assert line.startswith("    ")
    assert markdown.count("脚注伴随译文") == 1
    assert Path(output["html"]).read_text(encoding="utf-8").count("脚注伴随译文") == 1


@pytest.mark.parametrize("failure", ["copy", "document", "external"])
@pytest.mark.parametrize("missing", [False, True])
def test_render_rolls_back_shared_mathjax(project, monkeypatch, failure, missing):
    bid = "sample-one-b001"
    packet = create_workflow_packet(project, "transcribe", [bid])
    write_json(project / "candidate.json", {"packet_id": packet["packet_id"], "author_task_id": "worker",
        "model": packet["model"], "reasoning_effort": packet["reasoning_effort"], "image_evidence": packet["required_images"],
        "candidates": [{"asset_id": aid, "format": "latex", "content": "1+1=2"} for aid in packet["asset_ids"]]})
    submit_candidates(project, project / "candidate.json")
    outputs = rendering.render_project(project, None, batch_id=bid, allow_draft=True)
    target = project / "output/mathjax"
    runtime_files = read_json(Path(str(files("littrans").joinpath("vendor/mathjax/manifest.json"))))["files"]
    paths = [target / relative for relative in runtime_files]
    if missing:
        paths[1].unlink()
    before = {p: p.read_bytes() if p.is_file() else None for p in paths}
    documents = {Path(outputs[key]): Path(outputs[key]).read_bytes() for key in ("html", "markdown", "quality", "render_qa")}
    original = rendering.install_mathjax
    def install(output):
        original(output)
        paths[0].write_bytes(b"partially copied replacement")
        if failure == "copy":
            raise KeyboardInterrupt("copy interrupted")
    monkeypatch.setattr(rendering, "install_mathjax", install)
    def fail(*args, **kwargs):
        raise RuntimeError("publication failed")
    if failure == "document":
        monkeypatch.setattr(rendering, "_write_quality_summary", fail)
    elif failure == "external":
        config = load_project(project)
        config.external_review = ExternalReviewConfig(reviewers=[ExternalReviewerConfig(id="test", driver="claude-code", command="claude", model="test")])
        save_project(project, config)
        monkeypatch.setattr(rendering, "_write_external_review_summary_set", fail)
    with pytest.raises((RuntimeError, KeyboardInterrupt)):
        rendering.render_project(project, None, batch_id=bid, allow_draft=True)
    assert {p: p.read_bytes() if p.is_file() else None for p in paths} == before
    assert {p: p.read_bytes() for p in documents} == documents
