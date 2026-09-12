from pathlib import Path

import pytest
from test_asset_representation import candidate_input
from test_asset_representation import project as asset_project
from test_efficiency_v4 import _submit
from test_footnotes_v6 import _linked_notes
from test_pr10_round8 import submit
from test_workflow_v6 import project as workflow_project

from littrans import fidelity
from littrans.models import SourceUnit, TableData, TranslationRecord, UnitKind
from littrans.quality import run_qa
from littrans.rendering import _unit_html
from littrans.representations import representation_status, submit_candidates
from littrans.storage import read_json, read_jsonl, write_json

asset_root = asset_project
project = workflow_project


@pytest.mark.parametrize("target", [r"`[^1]`", r"$[^1]$", r"\([^1]\)", r"\[^1]", "[^1]", "[^1] [^1]"])
def test_qa_counts_real_footnotes(tmp_path, target):
    root, manifests, _ = _linked_notes(tmp_path)
    _submit(root, manifests[0].batch_id, target_text="译文 " + target)
    errors = {e.code for e in run_qa(root, manifests[0].batch_id).errors}
    assert ("footnote-call-mismatch" in errors) is (target != "[^1]")


@pytest.mark.parametrize("damage", ["missing", "invalid-json", "changed"])
def test_candidate_damage_recovers(asset_root, damage):
    original = candidate_input(asset_root)
    submit_candidates(asset_root, asset_root / "candidate.json")
    index = read_json(asset_root / "evidence/representations/index.json")
    path = asset_root / "evidence/representations/candidates" / f"{index['candidates']['a1']}.json"
    if damage == "missing":
        path.unlink()
    elif damage == "invalid-json":
        path.write_text("{", encoding="utf-8")
    else:
        data = read_json(path)
        data["content"] = "x=2"
        write_json(path, data)
    state = representation_status(asset_root, ["a1"])["assets"]["a1"]
    assert state["state"] == "transcribe" and state["semantic_uncertainty"]
    fresh = candidate_input(asset_root)
    assert fresh["packet_id"] != original["packet_id"]
    submit_candidates(asset_root, asset_root / "candidate.json")
    assert representation_status(asset_root, ["a1"])["assets"]["a1"]["state"] == "asset-audit"


def test_source_table_assets_keep_structure(asset_root):
    from littrans.representations import resolve_asset_html
    unit = SourceUnit(unit_id="table", page=1, bbox=(0, 0, 1, 1), kind=UnitKind.TABLE,
                      source_text="Label {{asset:a1}}", source_hash="x", confidence=1,
                      table=TableData(rows=[["Label", "{{asset:a1}}"]], column_count=2, header_rows=0))
    result = _unit_html(unit, None, source_view=True)
    assert "<table>" in result and result.count("<td>") == 2
    assert "{{asset:a1}}" in result
    rendered = resolve_asset_html(asset_root, result, asset_root / "output")
    assert "<table>" in rendered and "<img" in rendered and "{{asset:" not in rendered


@pytest.mark.parametrize("interrupted", [False, True])
def test_source_override_retires_translations(project, monkeypatch, interrupted):
    submit(project, "sample-one-b001")
    before = read_jsonl(project / "translations/current.jsonl", TranslationRecord)
    packet = fidelity.build_source_review_packet(project, "1")
    payload = read_json(Path(packet["packet_path"]))
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "test"
    replacement = [{"unit_id": "renamed-" + u["unit_id"], "kind": u["kind"],
                    "bbox": u["bbox"], "source_markdown": u["source_markdown"] or u["source_text"]}
                   for u in payload["pages"][0]["units"]]
    review["pages"][0]["override"] = {"units": replacement}
    path = project / "override.json"
    write_json(path, review)
    if interrupted:
        old_units = (project / "derived/units.jsonl").read_bytes()
        old_translations = (project / "translations/current.jsonl").read_bytes()
        original_write = fidelity.write_jsonl
        def interrupt(target, records):
            original_write(target, records)
            if target == project / "derived/units.jsonl":
                raise KeyboardInterrupt
        monkeypatch.setattr(fidelity, "write_jsonl", interrupt)
        with pytest.raises(KeyboardInterrupt):
            fidelity.import_source_review(project, path, True)
        assert (project / "derived/units.jsonl").read_bytes() == old_units
        assert (project / "translations/current.jsonl").read_bytes() == old_translations
        assert not (project / "translations/source-retired.jsonl").exists()
        return
    fidelity.import_source_review(project, path, True)
    assert not read_jsonl(project / "translations/current.jsonl", TranslationRecord)
    retired = read_jsonl(project / "translations/source-retired.jsonl", TranslationRecord)
    assert {r.unit_id for r in retired} == {r.unit_id for r in before}


@pytest.mark.parametrize("reverse", [False, True])
def test_same_import_dependency_approval_is_deferred(tmp_path, reverse):
    root, _, _ = _linked_notes(tmp_path)
    packet = fidelity.build_source_review_packet(root, "1-2")
    payload = read_json(Path(packet["packet_path"]))
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "test"
    for decision in review["pages"]:
        if decision["page"] == 2:
            u = payload["pages"][1]["units"][0]
            decision["override"] = {"units": [{"unit_id": u["unit_id"], "kind": "footnote",
                "bbox": u["bbox"], "footnote_number": "1", "source_markdown": "Changed note"}]}
        else:
            for key in ("viewed_original", "coverage_complete", "boundaries_complete", "reading_order_correct",
                        "grouping_checked", "layout_fallback_checked"):
                decision[key] = True
    if reverse:
        review["pages"].reverse()
    path = root / "mixed-review.json"
    write_json(path, review)
    result = fidelity.import_source_review(root, path, True)
    assert result["approved_pages"] == []
    assert result["deferred_pages"] == [1]
    assert result["requires_new_packet"]
