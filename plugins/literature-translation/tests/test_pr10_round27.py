from pathlib import Path

import pytest

from littrans import workflow
from littrans.models import SourceUnit, UnitKind
from littrans.rendering import _render_quality_errors, _unit_html


def _footnote(unit_id: str, number: str | None) -> SourceUnit:
    return SourceUnit(unit_id=unit_id, kind=UnitKind.FOOTNOTE, page=3, bbox=(0, 0, 1, 1),
                      source_text="note", source_hash="x", confidence=1.0, footnote_number=number)


@pytest.mark.parametrize("source_view", [True, False])
def test_unnumbered_footnotes_share_no_anchor(source_view: bool) -> None:
    """Detector-labelled footnotes without a marker must not collide on fn-*-0."""
    first, second = _footnote("p0003-b7", None), _footnote("p0003-b8", None)
    markup = _unit_html(first, None, source_view=source_view) + _unit_html(second, None, source_view=source_view)
    assert "-0\"" not in markup
    assert _render_quality_errors('<a id="p0003-b7"></a>\n<a id="p0003-b8"></a>', markup, [first, second]) == []
    numbered = _unit_html(_footnote("p0003-b9", "4"), None, source_view=source_view)
    assert f'id="fn-p3-{"source" if source_view else "target"}-4"' in numbered


def test_workflow_next_computes_each_asset_lane_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_efficiency_v4 import _make_project

    root, manifests = _make_project(tmp_path, pages=3, max_words=100)
    calls: list[str] = []

    def lane(root: Path, manifest, units):
        calls.append(manifest.batch_id)
        return {"states": {}, "pending": {"transcribe": [], "asset-audit": []}, "recovery": [], "complete": True}

    monkeypatch.setattr(workflow, "_asset_lane", lane)
    result = workflow.workflow_next(root)
    assert result["batch_ids"]
    assert sorted(calls) == sorted(set(calls))
