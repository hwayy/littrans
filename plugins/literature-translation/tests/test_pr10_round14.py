
import pymupdf as fitz
import pytest
from test_asset_representation import candidate_input, review_input
from test_asset_representation import project as asset_project
from test_fidelity_source import approve
from test_pr10_round8 import submit
from test_workflow_v6 import project as workflow_project

from littrans import fidelity
from littrans.batching import create_batches, load_manifest
from littrans.fidelity_models import load_assets
from littrans.models import SourceUnit, TableData, UnitKind
from littrans.quality import run_qa
from littrans.representations import (
    build_asset_packet,
    import_asset_review,
    representation_status,
    submit_candidates,
)
from littrans.storage import load_project, read_json, read_jsonl, write_json, write_jsonl
from littrans.workflow import create_workflow_packet, workflow_next

asset_root = asset_project
project = workflow_project


@pytest.mark.parametrize("damage", ["missing", "tampered"])
@pytest.mark.parametrize("verdict", ["reject", "unresolved", "accept"])
def test_unreadable_indexed_review_blocks(asset_root, damage, verdict):
    candidate_input(asset_root)
    submit_candidates(asset_root, asset_root / "candidate.json")
    review = review_input(asset_root, verdict)
    if verdict != "accept":
        review["decisions"][0]["semantic_uncertainty"] = "Meaning unclear"
    write_json(asset_root / "review.json", review)
    import_asset_review(asset_root, asset_root / "review.json", True)
    path = asset_root / "evidence/representations/reviews" / f"{review['packet_id']}.json"
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("{}", encoding="utf-8")
    state = representation_status(asset_root, ["a1"])["assets"]["a1"]
    assert state["state"] == "asset-audit"
    assert state["semantic_uncertainty"]
    fresh = review_input(asset_root, "accept")
    assert fresh["packet_id"] != review["packet_id"]
    import_asset_review(asset_root, asset_root / "review.json", True)
    assert representation_status(asset_root, ["a1"])["assets"]["a1"]["state"] == "verified"


@pytest.mark.parametrize("dependency", [False, True])
def test_pure_math_uncertainty_blocks_qa_and_dispatches_recovery(project, dependency):
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    asset = next(a for a in load_assets(project).values() if a.fragments[0].page == (2 if dependency else 1))
    for unit in units:
        if asset.id in unit.asset_content_hashes:
            unit.translatable = False
            if dependency:
                unit.parent_id = "cross-page-group"
        elif dependency and unit.page == 1:
            unit.parent_id = "cross-page-group"
    write_jsonl(project / "derived/units.jsonl", units)
    approve(project, "all")
    batch = load_manifest(project, "sample-one-b001") if dependency else create_batches(project, "1", prefix="pure-math")[0]
    submit(project, batch.batch_id)
    packet = build_asset_packet(project, [asset.id])
    write_json(project / "candidate.json", {
        "packet_id": packet["packet_id"], "author_task_id": "writer", "model": packet["model"],
        "reasoning_effort": packet["reasoning_effort"], "image_evidence": packet["required_images"],
        "candidates": [{"asset_id": asset.id, "format": "latex", "content": "1+1=2"}],
    })
    submit_candidates(project, project / "candidate.json")
    packet = build_asset_packet(project, [asset.id], "asset-audit")
    write_json(project / "review.json", {
        "packet_id": packet["packet_id"], "reviewer_task_id": "reviewer",
        "render_artifact_sha256": packet["render_artifact"]["sha256"],
        "render_manifest_sha256": packet["render_manifest_sha256"], "image_evidence": packet["required_images"],
        "decisions": [{"asset_id": asset.id, "candidate_sha256": packet["candidates"][asset.id]["candidate_sha256"],
                       "verdict": "reject", "visual_checked": True, "render_checked": True,
                       "semantic_uncertainty": "Meaning unclear"}],
    })
    import_asset_review(project, project / "review.json", True)
    assert workflow_next(project, start_at=batch.batch_id, through=batch.batch_id)["stage"] == "transcribe"
    assert "asset-semantic-uncertainty" in {e.code for e in run_qa(project, batch.batch_id).errors}
    assert asset.id in create_workflow_packet(project, "transcribe", [batch.batch_id])["asset_ids"]


@pytest.mark.parametrize("keep_asset", [True, False])
def test_table_asset_references_use_cells(project, keep_asset):
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    unit = next(u for u in units if u.page == 1 and u.asset_content_hashes)
    aid = next(iter(unit.asset_content_hashes))
    text = "Label {{asset:" + aid + "}}"
    unit.kind = UnitKind.TABLE
    unit.translatable = True
    unit.source_text = unit.source_markdown = text
    unit.table = TableData(rows=[[text]], column_count=1)
    write_jsonl(project / "derived/units.jsonl", units)
    approve(project, "all")
    batch = create_batches(project, "1", prefix="asset-table", unit_ids=[unit.unit_id])[0]
    def change(source, record):
        record.target_text = ""
        record.target_table = TableData(rows=[["标签" + ("{{asset:" + aid + "}}" if keep_asset else "")]], column_count=1)
    submit(project, batch.batch_id, change)
    errors = {e.code for e in run_qa(project, batch.batch_id).errors}
    assert ("asset-reference-mismatch" in errors) is (not keep_asset)


@pytest.mark.parametrize("name", ["original.svg", "original.png"])
def test_unreceipted_cache_is_regenerated(project, name):
    config = load_project(project)
    region = {"kind": "math", "bbox": [45, 80, 110, 105]}
    with fitz.open(config.source(project)) as doc:
        first = fidelity._asset(project, doc, 1, config.source_sha256, region, [])
        folder = (project / first.fragments[0].png_path).parent
        receipt = folder / "evidence.json"
        receipt.unlink()
        (folder / name).write_bytes(b"truncated")
        fidelity._asset(project, doc, 1, config.source_sha256, region, [])
    assert (folder / name).read_bytes() != b"truncated"
    assert set(read_json(receipt)["files"]) == {"original.svg", "original.png"}
    assert not (folder / "original.pdf").exists() and first.fragments[0].pdf_path is None
    assert fitz.Pixmap(str(folder / "original.png")).width > 0
