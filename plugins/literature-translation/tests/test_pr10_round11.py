from pathlib import Path

import pytest
from test_pr10_round8 import submit
from test_workflow_v6 import project as workflow_project

from littrans import fidelity
from littrans.batching import create_batches, refresh_batch
from littrans.models import TranslationRecord
from littrans.storage import load_project, read_json, save_project, write_json
from littrans.workflow import create_workflow_packet

project = workflow_project


def test_emitted_batch_schema_accepts_asset_evidence_shape(project):
    batch = create_batches(project, "1", prefix="schema-evidence")[0]
    schema = read_json(project / "batches" / batch.batch_id / "output-schema.json")
    actual = TranslationRecord.model_json_schema()
    for field in ("image_evidence", "asset_translations"):
        assert schema["properties"][field] == actual["properties"][field]
    assert schema["$defs"]["AssetTranslation"] == actual["$defs"]["AssetTranslation"]
    assert schema["additionalProperties"] is False
    path = project / "batches" / batch.batch_id / "output-schema.json"
    write_json(path, {"type": "object", "properties": {}})
    refresh_batch(project, batch.batch_id)
    assert read_json(path) == schema


@pytest.mark.parametrize("stage", ["translate", "revise"])
def test_packet_identity_binds_host_model_and_effort(project, stage):
    if stage == "revise":
        submit(project, "sample-one-b001")
    config = load_project(project)
    config.agent_models["claude"] = {"translate": "claude-test", "reasoning_effort": "high"}
    config.agent_models["cursor"] = {"translate": "cursor-test", "reasoning_effort": "max"}
    save_project(project, config)
    first = create_workflow_packet(project, stage, ["sample-one-b001"], host="claude")
    second = create_workflow_packet(project, stage, ["sample-one-b001"], host="cursor")
    assert first.packet_id != second.packet_id
    assert (first.host, first.model, first.reasoning_effort) == ("claude", "claude-test", "high")
    assert (second.host, second.model, second.reasoning_effort) == ("cursor", "cursor-test", "max")
    assert create_workflow_packet(project, stage, ["sample-one-b001"], host="claude") == first
    config.agent_models["claude"]["reasoning_effort"] = "max"
    save_project(project, config)
    third = create_workflow_packet(project, stage, ["sample-one-b001"], host="claude")
    assert third.packet_id != first.packet_id
    config.agent_models["claude"]["translate"] = "another-model"
    save_project(project, config)
    fourth = create_workflow_packet(project, stage, ["sample-one-b001"], host="claude")
    assert fourth.packet_id != third.packet_id


@pytest.mark.parametrize("duplicate", [False, True])
def test_override_validates_final_cross_page_graph(project, duplicate):
    packet = fidelity.build_source_review_packet(project, "all")
    payload = read_json(Path(packet["packet_path"]))
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "test"
    for page, decision in zip(payload["pages"], review["pages"], strict=True):
        replacements = [
            {
                "unit_id": u["unit_id"],
                "source_markdown": u["source_markdown"] or u["source_text"],
                "kind": u["kind"],
                "bbox": u["bbox"],
            }
            for u in page["units"]
        ]
        if page["page"] == 1:
            replacements[0]["footnote_refs"] = ["new-note"] * (2 if duplicate else 1)
        else:
            replacements[0].update(unit_id="new-note", kind="footnote", footnote_number="1")
        decision["override"] = {"units": replacements}
    path = project / "forward-reference.json"
    write_json(path, review)
    if duplicate:
        with pytest.raises(ValueError, match="footnote"):
            fidelity.import_source_review(project, path, True)
        assert (
            fidelity.build_source_review_packet(project, "all")["packet_id"] == packet["packet_id"]
        )
    else:
        assert fidelity.import_source_review(project, path, True)["requires_new_packet"]
        assert (
            fidelity.build_source_review_packet(project, "all")["packet_id"] != packet["packet_id"]
        )


@pytest.mark.parametrize("refs", [["typo"], ["p0002-b0"], ["p0002-b0", "p0002-b0"]])
def test_invalid_override_footnotes_do_not_publish(project, refs):
    packet = fidelity.build_source_review_packet(project, "1")
    payload = read_json(Path(packet["packet_path"]))
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "test"
    source_units = [
        {
            "unit_id": u["unit_id"],
            "source_markdown": u["source_markdown"] or u["source_text"],
            "kind": u["kind"],
            "bbox": u["bbox"],
            "footnote_refs": refs,
        }
        for u in payload["pages"][0]["units"]
    ]
    review["pages"][0]["override"] = {"units": source_units}
    path = project / "invalid-ref.json"
    write_json(path, review)
    paths = [
        project / "derived/units.jsonl",
        project / "derived/fidelity-assets.jsonl",
        project / "derived/fidelity-pages/p0001.json",
        project / "evidence/pages/fidelity-p0001.review.json",
    ]
    before = {p: p.read_bytes() for p in paths}
    with pytest.raises(ValueError, match="footnote"):
        fidelity.import_source_review(project, path, True)
    assert {p: p.read_bytes() for p in paths} == before
    assert fidelity.build_source_review_packet(project, "1")["packet_id"] == packet["packet_id"]
