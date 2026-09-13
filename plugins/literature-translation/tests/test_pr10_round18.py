from pathlib import Path

import pytest
from test_fidelity_source import approve
from test_pr10_round8 import submit
from test_workflow_v6 import project as workflow_project

from littrans.batching import create_batches
from littrans.fidelity import _cached_layout
from littrans.models import AssetTranslation, FigureLabel, SourceUnit, TableData, UnitKind
from littrans.rendering import render_project
from littrans.storage import read_jsonl, write_json, write_jsonl

project = workflow_project


@pytest.mark.parametrize("damage", ["{", "[]", "not json"])
@pytest.mark.parametrize("matching", [False, True])
def test_layout_scan_skips_unreadable(tmp_path, damage, matching):
    folder = tmp_path / "derived/fidelity-layout"
    folder.mkdir(parents=True)
    (folder / "000-bad.json").write_text(damage, encoding="utf-8")
    image = "evidence/pages/fidelity-p0001.png"
    valid = {"status": "ok", "fingerprint": "wanted", "pages": {str((tmp_path / image).resolve()): []}}
    if matching:
        write_json(folder / "zzz-valid.json", valid)
    result = _cached_layout(tmp_path, {"layout_status": "ok", "layout_fingerprint": "wanted", "page_image": image})
    assert result == valid if matching else result["status"] == "unavailable"


@pytest.mark.parametrize("companion_kind", ["text", "table", "labels"])
def test_continued_table_preserves_later_companions(project, companion_kind):
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    tail = next(u for u in units if u.page == 1 and u.asset_content_hashes)
    head = tail.model_copy(deep=True, update={"unit_id": "table-head", "asset_refs": [], "asset_content_hashes": {}})
    units.insert(units.index(tail), head)
    fragments = [head, tail]
    for index, unit in enumerate(fragments):
        aid = next(iter(unit.asset_content_hashes), None)
        text = "Label" + (" {{asset:" + aid + "}}" if aid else "")
        unit.kind = UnitKind.TABLE
        unit.translatable = True
        unit.source_text = unit.source_markdown = text
        unit.table = TableData(rows=[[text]], column_count=1, header_rows=0)
        unit.continues_from_previous = index == 1
        unit.continued_to_next = index == 0
    write_jsonl(project / "derived/units.jsonl", units)
    approve(project, "all")
    batch = create_batches(project, "1", prefix="continued-companions")[0]
    def change(source, record):
        record.target_text = ""
        aid = next(iter(source.asset_content_hashes), None)
        record.target_table = TableData(rows=[["标签" + ("{{asset:" + aid + "}}" if aid else "")]], column_count=1, header_rows=0)
        if aid:
            companion = AssetTranslation(asset_id=aid)
            if companion_kind == "text":
                companion.target_text = "后续片段伴随译文"
            elif companion_kind == "table":
                companion.target_table = TableData(rows=[["后续片段伴随译文"]], column_count=1, header_rows=0)
            else:
                companion.figure_labels = [FigureLabel(source="Label", target="后续片段伴随译文")]
            record.asset_translations = [companion]
    submit(project, batch.batch_id, change)
    outputs = render_project(project, None, batch_id=batch.batch_id, allow_draft=True)
    for kind in ("markdown", "html"):
        assert Path(outputs[kind]).read_text(encoding="utf-8").count("后续片段伴随译文") == 1
