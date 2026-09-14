import pytest
from test_asset_representation import project as asset_project
from test_pr10_round8 import submit
from test_workflow_v6 import project as workflow_project

from littrans.batching import create_batches
from littrans.models import AssetTranslation, FigureLabel, TableData, TranslationRecord
from littrans.quality import run_qa
from littrans.rendering import _asset_companions
from littrans.representations import validate_asset_translations
from littrans.storage import read_jsonl, write_jsonl

asset_root = asset_project
project = workflow_project


def companion(asset_id, field, reference):
    item = AssetTranslation(asset_id=asset_id, target_text="中文说明")
    if field == "text":
        item.target_text += reference
    elif field == "table":
        item.target_table = TableData(rows=[["中文" + reference]], column_count=1)
    else:
        item.figure_labels = [FigureLabel(source=reference if field == "label-source" else "Label",
                                         target="中文" + (reference if field == "label-target" else ""))]
    return item


@pytest.mark.parametrize("field", ["text", "table", "label-source", "label-target"])
@pytest.mark.parametrize("reference,live", [("[^1]", True), ("[^999]", True), ("`[^1]`", False), (r"\[^1]", False)])
def test_companion_footnote_validation_matches_rendering(asset_root, field, reference, live):
    item = companion("a1", field, reference)
    record = TranslationRecord(unit_id="u1", source_hash="hash", target_text="译文[^1]", asset_translations=[item])
    _, rendered = _asset_companions(record)
    assert ('class="footnote-ref"' in rendered) is live
    errors = validate_asset_translations(asset_root, "Source[^1] {{asset:a1}}", [item])
    assert any(e["code"] == "footnote-call-in-companion" for e in errors) is live


@pytest.mark.parametrize("field", ["text", "table", "label-source", "label-target"])
def test_qa_rejects_companion_footnotes_after_cached_pass(project, field):
    batch = create_batches(project, "1", prefix="footnotes", untranslated_only=False)[0]
    submit(project, batch.batch_id)
    assert run_qa(project, batch.batch_id).passed
    records = read_jsonl(project / "translations/current.jsonl", TranslationRecord)
    record = next(r for r in records if "{{asset:" in r.target_text)
    asset_id = record.target_text.split("{{asset:")[1].split("}}")[0]
    record.asset_translations = [companion(asset_id, field, "[^999]")]
    write_jsonl(project / "translations/current.jsonl", records)
    report = run_qa(project, batch.batch_id)
    assert not report.passed
    assert "footnote-call-in-companion" in {e.code for e in report.errors}
