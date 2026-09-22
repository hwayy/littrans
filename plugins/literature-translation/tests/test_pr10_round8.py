from pathlib import Path

import pymupdf as fitz
import pytest
from test_fidelity_source import approve
from test_workflow_v6 import project as workflow_project
from typer.testing import CliRunner

from littrans import fidelity
from littrans.batching import create_batches, load_manifest, refresh_batch
from littrans.cli import app
from littrans.context_packets import original_context
from littrans.fidelity_models import load_assets
from littrans.models import (
    AssetTranslation,
    RoleDispatch,
    SourceUnit,
    TableData,
    TranslationRecord,
    UnitKind,
)
from littrans.quality import run_qa
from littrans.storage import (
    load_project,
    read_json,
    read_jsonl,
    save_project,
    write_json,
    write_jsonl,
    write_yaml,
)
from littrans.translation import submit_translation
from littrans.workflow import create_workflow_packet, workflow_next, workflow_status

project = workflow_project


def submit(root, bid, changes=None):
    manifest = load_manifest(root, bid)
    units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    records = []
    for unit in units:
        if unit.unit_id in manifest.translatable_unit_ids:
            record = TranslationRecord(
                unit_id=unit.unit_id,
                source_hash=unit.source_hash,
                target_text=unit.source_text.replace("The identity holds.", "该恒等式成立。"),
                image_evidence=original_context(root, [unit])["required_images"],
            )
            if changes:
                changes(unit, record)
            records.append(record)
    path = root / "new-translations.jsonl"
    write_jsonl(path, records)
    submit_translation(root, bid, path)
    return records


@pytest.mark.parametrize("host", ["claude", "cursor"])
def test_status_explicit_host(project, monkeypatch, host):
    monkeypatch.setenv("CODEX_THREAD_ID", "test")
    config = load_project(project)
    config.agent_models[host] = {
        "translate": RoleDispatch(model="selected-host-model", reasoning_effort="high")
    }
    save_project(project, config)
    status = workflow_status(project, ["sample-one-b001"], host=host)
    assert status["host"] == host
    assert status["ready_tasks"][0]["model"] == "selected-host-model"
    result = CliRunner().invoke(
        app, ["workflow", "status", str(project), "--batch-ids", "sample-one-b001", "--host", host]
    )
    assert result.exit_code == 0, result.output
    assert "selected-host-model" in result.output


@pytest.mark.parametrize("keep_call", [False, True])
def test_qa_table_footnote_cells(project, keep_call):
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    original = next(u for u in units if u.page == 1 and u.kind == UnitKind.PARAGRAPH)
    table = original.model_copy(
        deep=True, update={"unit_id": "new-table", "asset_refs": [], "asset_content_hashes": {}}
    )
    table.kind = UnitKind.TABLE
    table.parent_id = None
    table.source_text = table.source_markdown = "Label[^1]"
    table.table = TableData(rows=[["Label[^1]"]], column_count=1)
    table.footnote_refs = ["table-note"]
    note = table.model_copy(deep=True, update={"unit_id": "table-note", "kind": UnitKind.FOOTNOTE,
        "source_text": "Note", "source_markdown": "Note", "table": None, "footnote_refs": [], "footnote_number": "1"})
    units.insert(1, table)
    units.insert(2, note)
    write_jsonl(project / "derived/units.jsonl", units)
    approve(project, "all")
    table_batch = create_batches(project, "1", prefix="table-test", unit_ids=[table.unit_id])[0]

    def change(unit, record):
        if unit.unit_id == table.unit_id:
            record.target_text = ""
            record.target_table = TableData(
                rows=[["标签[^1]" if keep_call else "标签"]], column_count=1
            )

    submit(project, table_batch.batch_id, change)
    errors = {e.code for e in run_qa(project, table_batch.batch_id).errors}
    assert ("footnote-call-mismatch" in errors) is (not keep_call)


def test_companion_cannot_cover_missing_prose(project):
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    unit = next(u for u in units if u.page == 1 and u.kind == UnitKind.PARAGRAPH)
    aid = next(iter(load_assets(project)))
    unit.source_text = unit.source_markdown = "The identity holds in 2020. {{asset:" + aid + "}}"
    unit.protected_tokens = ["2020"]
    write_jsonl(project / "derived/units.jsonl", units)
    approve(project, "all")
    write_yaml(
        project / "glossary/approved.yaml", {"terms": [{"source": "identity", "target": "恒等式"}]}
    )

    def change(source, record):
        if source.unit_id == unit.unit_id:
            record.target_text = "这一结论成立。{{asset:" + aid + "}}"
            record.asset_translations = [AssetTranslation(asset_id=aid, target_text="2020 恒等式")]

    submit(project, "sample-one-b001", change)
    errors = {e.code for e in run_qa(project, "sample-one-b001").errors}
    assert {"number-mismatch", "protected-token-missing", "approved-term-missing"} <= errors


@pytest.mark.parametrize(
    "change", [{"kind": "mixed-region"}, {"display": False}, {"grouping_pending": True}]
)
def test_preserved_asset_semantics_change_identity(project, change):
    asset = next(iter(load_assets(project).values()))
    if "display" in change:
        change = {"display": not asset.display}
    config = load_project(project)
    with fitz.open(config.source(project)) as doc:
        changed = fidelity._asset(
            project,
            doc,
            asset.fragments[0].page,
            config.source_sha256,
            {"preserve_asset_id": asset.id, **change},
            [],
        )
    assert changed.content_sha256 != asset.content_sha256
    original_unit = fidelity._make_unit(
        1, "prose", "{{asset:" + asset.id + "}}", (0, 0, 1, 1), {asset.id: asset}
    )
    changed_unit = fidelity._make_unit(
        1, "prose", original_unit.source_text, (0, 0, 1, 1), {asset.id: changed}
    )
    assert original_unit.source_hash != changed_unit.source_hash


@pytest.mark.parametrize("translated_sibling", [False, True])
def test_untranslated_batch_retains_group_context(project, translated_sibling):
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    first = next(u for u in units if u.page == 1)
    sibling = first.model_copy(
        deep=True,
        update={
            "unit_id": "sibling",
            "source_text": "Context",
            "source_markdown": "Context",
            "asset_refs": [],
            "asset_content_hashes": {},
            "translatable": False,
        },
    )
    units.insert(1, sibling)
    group = [u for u in units if u.page == 1]
    for unit in group:
        unit.parent_id = "logical-paragraph"
    first = next(u for u in group if u.translatable)
    sibling = next(u for u in group if u.unit_id != first.unit_id)
    if translated_sibling:
        sibling.translatable = True
        write_jsonl(
            project / "translations/current.jsonl",
            [
                TranslationRecord(
                    unit_id=sibling.unit_id,
                    source_hash=sibling.source_hash,
                    target_text="Existing translation",
                )
            ],
        )
    write_jsonl(project / "derived/units.jsonl", units)
    approve(project, "all")
    batch = create_batches(project, "1", prefix="remaining", untranslated_only=True)[0]
    assert batch.unit_ids == [u.unit_id for u in group]
    assert batch.translatable_unit_ids == [first.unit_id]
    assert sibling.unit_id in batch.read_only_unit_ids
    assert refresh_batch(project, batch.batch_id).translatable_unit_ids == [first.unit_id]
    assert workflow_status(project, [batch.batch_id])["stage"] == "translate"
    create_workflow_packet(project, "translate", [batch.batch_id])
    records = submit(project, batch.batch_id)
    records.append(
        TranslationRecord(
            unit_id=sibling.unit_id, source_hash=sibling.source_hash, target_text="Wrong overwrite"
        )
    )
    path = project / "bad-context-edit.jsonl"
    write_jsonl(path, records)
    with pytest.raises(ValueError, match="coverage mismatch"):
        submit_translation(project, batch.batch_id, path)
    if translated_sibling:
        assert (
            next(
                r
                for r in read_jsonl(project / "translations/current.jsonl", TranslationRecord)
                if r.unit_id == sibling.unit_id
            ).target_text
            == "Existing translation"
        )


def test_dependency_uncertainty_dispatches_editable_owner(project):
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    caller = next(u for u in units if u.page == 1 and u.translatable)
    note = next(u for u in units if u.page == 2 and u.translatable)
    note.kind = UnitKind.FOOTNOTE
    note.footnote_number = "1"
    caller.footnote_refs = [note.unit_id]
    caller.source_text += " Call[^1]"
    caller.source_markdown = caller.source_text
    write_jsonl(project / "derived/units.jsonl", units)
    approve(project, "all")
    submit(project, "sample-one-b001")

    def uncertain(unit, record):
        if unit.unit_id == note.unit_id:
            record.uncertainties = ["Meaning needs clarification"]

    submit(project, "sample-two-b001", uncertain)
    assert not run_qa(project, "sample-one-b001").passed
    assert not run_qa(project, "sample-two-b001").passed
    result = workflow_next(project)
    assert result["stage"] == "revise"
    assert result["batch_ids"] == ["sample-two-b001"]
    bounded = workflow_next(project, start_at="sample-one-b001", through="sample-one-b001", limit=1)
    assert bounded["batch_ids"] == ["sample-two-b001"]
    assert bounded["requested_batch_ids"] == ["sample-one-b001"]
    status = workflow_status(project, ["sample-one-b001"])
    assert status["ready_tasks"][0]["batch_id"] == "sample-two-b001"
    packet = create_workflow_packet(project, "revise", result["batch_ids"])
    assert note.unit_id in load_manifest(project, packet.batch_ids[0]).translatable_unit_ids
    submit(project, "sample-two-b001")
    assert workflow_next(project)["stage"] == "qa"


def test_preserved_semantic_edit_requires_new_translation_evidence(project):
    submit(project, "sample-one-b001")
    assert run_qa(project, "sample-one-b001").passed
    before = read_jsonl(project / "derived/units.jsonl", SourceUnit)[0].source_hash
    packet = fidelity.build_source_review_packet(project, "1")
    payload = read_json(Path(packet["packet_path"]))
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "semantic-edit-test"
    review["pages"][0]["override"] = {
        "regions": [
            {"preserve_asset_id": asset["id"], "kind": "mixed-region"}
            for asset in payload["pages"][0]["assets"]
        ]
    }
    path = project / "semantic-override.json"
    write_json(path, review)
    fidelity.import_source_review(project, path, True)
    approve(project, "all")
    assert read_jsonl(project / "derived/units.jsonl", SourceUnit)[0].source_hash != before
    errors = {e.code for e in run_qa(project, "sample-one-b001").errors}
    assert "source-hash-mismatch" in errors
