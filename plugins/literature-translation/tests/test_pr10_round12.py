from pathlib import Path

import pytest
from test_pr10_round8 import submit
from test_workflow_v6 import project as workflow_project

from littrans import fidelity
from littrans.models import SourceUnit, UnitKind
from littrans.quality import run_qa
from littrans.representations import import_asset_review, representation_status, submit_candidates
from littrans.storage import read_json, write_json
from littrans.workflow import create_workflow_packet, workflow_next, workflow_status

project = workflow_project


@pytest.mark.parametrize(
    "numbers,refs", [(["2"], ["note0"]), (["1", "1"], ["note0", "note1"]), (["1"], [])]
)
def test_footnote_graph_matches_explicit_numbers(numbers, refs):
    caller = SourceUnit(
        unit_id="caller",
        kind=UnitKind.PARAGRAPH,
        page=1,
        bbox=(0, 0, 1, 1),
        source_text="Call[^1]",
        source_hash="x",
        confidence=1,
        footnote_refs=refs,
    )
    notes = [
        caller.model_copy(
            update={
                "unit_id": f"note{i}",
                "kind": UnitKind.FOOTNOTE,
                "source_text": "Note",
                "footnote_refs": [],
                "footnote_number": number,
            }
        )
        for i, number in enumerate(numbers)
    ]
    with pytest.raises(ValueError, match="footnote"):
        fidelity._validate_footnote_relationships([caller, *notes])


@pytest.mark.parametrize("operation", ["prepare", "import"])
def test_source_authority_rolls_back_keyboard_interrupt(project, monkeypatch, operation):
    packet = fidelity.build_source_review_packet(project, "1")
    payload = read_json(Path(packet["packet_path"]))
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "test"
    review["pages"][0]["override"] = {
        "units": [
            {
                "unit_id": u["unit_id"],
                "source_markdown": (u["source_markdown"] or u["source_text"]) + " Changed",
                "kind": u["kind"],
                "bbox": u["bbox"],
            }
            for u in payload["pages"][0]["units"]
        ]
    }
    review_path = project / "interrupted-review.json"
    write_json(review_path, review)
    before_paths = [
        project / "derived/units.jsonl",
        project / "derived/fidelity-assets.jsonl",
        project / "derived/fidelity-pages/p0001.json",
        project / "evidence/pages/fidelity-p0001.review.json",
    ]
    before = {p: p.read_bytes() for p in before_paths}
    original = fidelity.write_json

    def interrupted(path, data):
        original(path, data)
        if path == project / "derived/fidelity-pages/p0001.json":
            raise KeyboardInterrupt

    monkeypatch.setattr(fidelity, "write_json", interrupted)
    with pytest.raises(KeyboardInterrupt):
        if operation == "prepare":
            fidelity.prepare_source(project, "1", replace=True, allow_missing_layout=True)
        else:
            fidelity.import_source_review(project, review_path, True)
    assert {p: p.read_bytes() for p in before_paths} == before
    assert not (project / ".littrans-write-lock").exists()


@pytest.mark.parametrize("literal", [r"\[^1]", r"\(x[^1]\)", "`[^1]`", "$x[^1]$"])
def test_footnote_graph_ignores_literals_and_allows_repeated_calls(literal):
    caller = SourceUnit(
        unit_id="caller",
        kind=UnitKind.PARAGRAPH,
        page=1,
        bbox=(0, 0, 1, 1),
        source_text=literal + " Call[^12] again[^12]",
        source_hash="x",
        confidence=1,
        footnote_refs=["note"],
    )
    note = caller.model_copy(
        update={
            "unit_id": "note",
            "kind": UnitKind.FOOTNOTE,
            "source_text": "Note",
            "footnote_refs": [],
            "footnote_number": "12",
        }
    )
    fidelity._validate_footnote_relationships([caller, note])


@pytest.mark.parametrize("verdict", ["reject", "unresolved"])
def test_uncertain_asset_has_editable_recovery(project, verdict):
    bid = "sample-one-b001"
    submit(project, bid)
    transcribe = create_workflow_packet(project, "transcribe", [bid])
    aid = transcribe["asset_ids"][0]

    def candidate(packet):
        path = project / "candidate-recovery.json"
        write_json(
            path,
            {
                "packet_id": packet["packet_id"],
                "author_task_id": "writer",
                "model": packet["model"],
                "reasoning_effort": packet["reasoning_effort"],
                "image_evidence": packet["required_images"],
                "candidates": [{"asset_id": aid, "format": "latex", "content": "1+1=2"}],
            },
        )
        submit_candidates(project, path)

    candidate(transcribe)
    audit = create_workflow_packet(project, "asset-audit", [bid])
    path = project / "uncertain-review.json"
    write_json(
        path,
        {
            "packet_id": audit["packet_id"],
            "reviewer_task_id": "reviewer",
            "render_artifact_sha256": audit["render_artifact"]["sha256"],
            "render_manifest_sha256": audit["render_manifest_sha256"],
            "image_evidence": audit["required_images"],
            "decisions": [
                {
                    "asset_id": aid,
                    "candidate_sha256": audit["candidates"][aid]["candidate_sha256"],
                    "verdict": verdict,
                    "visual_checked": True,
                    "render_checked": True,
                    "semantic_uncertainty": "Meaning unclear",
                }
            ],
        },
    )
    import_asset_review(project, path, True)
    assert not run_qa(project, bid).passed
    status = workflow_status(project, [bid])
    assert not status["assets_complete"]
    assert status["optional_asset_tasks"][0]["stage"] == "transcribe"
    assert workflow_next(project, start_at=bid, through=bid)["stage"] == "transcribe"
    recovery = create_workflow_packet(project, "transcribe", [bid])
    assert (
        recovery["revision_context"][aid]["review_decision"]["semantic_uncertainty"]
        == "Meaning unclear"
    )
    candidate(recovery)
    assert representation_status(project, [aid])["assets"][aid]["state"] == "asset-audit"
    assert not representation_status(project, [aid])["assets"][aid]["semantic_uncertainty"]
    assert workflow_next(project, start_at=bid, through=bid)["stage"] == "qa"
