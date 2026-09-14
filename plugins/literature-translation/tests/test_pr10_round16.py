from pathlib import Path

import pytest
from test_fidelity_source import approve
from test_pr10_round8 import submit
from test_workflow_v6 import project as workflow_project

from littrans.batching import create_batches
from littrans.models import SourceUnit, TableData, UnitKind
from littrans.rendering import render_project
from littrans.source_render import render_source_review
from littrans.storage import read_jsonl, write_jsonl

project = workflow_project


@pytest.mark.parametrize("originals_only", [False, True])
def test_asset_table_complete_reading_outputs(project, originals_only):
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    unit = next(u for u in units if u.page == 1 and u.asset_content_hashes)
    aid = next(iter(unit.asset_content_hashes))
    unit.kind = UnitKind.TABLE
    unit.translatable = True
    unit.source_text = unit.source_markdown = "SOURCE_LABEL {{asset:" + aid + "}}"
    unit.table = TableData(rows=[["SOURCE_LABEL", "{{asset:" + aid + "}}"]], column_count=2, header_rows=0)
    write_jsonl(project / "derived/units.jsonl", units)
    approve(project, "all")
    batch = create_batches(project, "1", prefix="render-table", unit_ids=[unit.unit_id])[0]
    def change(source, record):
        record.target_text = ""
        record.target_table = TableData(rows=[["译文标签", "{{asset:" + aid + "}}"]], column_count=2, header_rows=0)
    submit(project, batch.batch_id, change)
    output = render_project(project, None, batch_id=batch.batch_id, allow_draft=True, originals_only=originals_only)
    markdown = Path(output["markdown"]).read_text(encoding="utf-8")
    bilingual = Path(output["html"]).read_text(encoding="utf-8")
    assert "SOURCE_LABEL" not in markdown
    assert markdown.count("译文标签") == 1
    assert bilingual.count("译文标签") == 1
    assert bilingual.count("SOURCE_LABEL") == 1
    assert bilingual.count("<table>") == 2
    assert "{{asset:" not in markdown + bilingual
    source = render_source_review(project, "1", name="source-table")
    source_html = Path(source["html"]).read_text(encoding="utf-8")
    assert "<table>" in source_html and "SOURCE_LABEL" in source_html
