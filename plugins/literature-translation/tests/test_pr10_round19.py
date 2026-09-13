
import pytest
from test_efficiency_v4 import _submit
from test_fidelity_source import approve
from test_footnotes_v6 import _linked_notes
from test_workflow_v6 import project as workflow_project

from littrans import fidelity
from littrans.batching import create_batches, refresh_batch
from littrans.models import SourceUnit, TranslationRecord
from littrans.quality import run_qa
from littrans.rendering import _inline_html, _markdown_footnote_calls
from littrans.semantics import explicit_footnote_numbers
from littrans.storage import read_jsonl, write_json, write_jsonl
from littrans.workflow import workflow_next

project = workflow_project


@pytest.mark.parametrize("stale", [False, True])
def test_missing_dependency_dispatches_owner(tmp_path, stale):
    root, manifests, units = _linked_notes(tmp_path)
    _submit(root, manifests[0].batch_id, target_text="译文[^1]")
    if stale:
        _submit(root, manifests[1].batch_id, target_text="注释")
        records = read_jsonl(root / "translations/current.jsonl", TranslationRecord)
        next(r for r in records if r.unit_id == units[1].unit_id).source_hash = "obsolete"
        write_jsonl(root / "translations/current.jsonl", records)
    report = run_qa(root, manifests[0].batch_id)
    assert any(e.code == ("source-hash-mismatch" if stale else "missing-translation") and e.unit_id == units[1].unit_id for e in report.errors)
    result = workflow_next(root, start_at=manifests[0].batch_id, through=manifests[0].batch_id)
    assert result["stage"] == ("qa" if stale else "translate") and result["batch_ids"] == [manifests[1].batch_id]


@pytest.mark.parametrize("text", [r"Price \$5 [^1] and $x$", r"Price \$5 [^1] and $$x$$"])
def test_currency_preserves_real_footnote(tmp_path, text):
    assert explicit_footnote_numbers(text) == {"1"}
    root, _, units = _linked_notes(tmp_path)
    caller, note = units[:2]
    assert _markdown_footnote_calls(text, caller, {u.unit_id: u for u in units}).count("[^fn-") == 1
    assert _inline_html(text, "source", {"1": note.unit_id}).count('class="footnote-ref"') == 1


def test_escaped_math_dollar_does_not_close_span():
    assert explicit_footnote_numbers(r"$x+\$5[^1]$ real[^2]") == {"2"}


@pytest.mark.parametrize("translated", [True, False])
def test_refresh_reopens_stale_read_only(project, translated):
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    first = next(u for u in units if u.page == 1 and u.translatable)
    sibling = first.model_copy(deep=True, update={"unit_id": "readonly", "source_text": "Context", "source_markdown": "Context",
        "asset_refs": [], "asset_content_hashes": {}, "parent_id": "group"})
    first.parent_id = "group"
    units.insert(units.index(first) + 1, sibling)
    write_jsonl(project / "derived/units.jsonl", units)
    write_jsonl(project / "translations/current.jsonl", [TranslationRecord(unit_id=sibling.unit_id, source_hash=sibling.source_hash, target_text="旧译文")])
    approve(project, "all")
    batch = create_batches(project, "1", prefix="readonly-check", untranslated_only=True)[0]
    assert sibling.unit_id in batch.read_only_unit_ids
    sibling.source_text = sibling.source_markdown = "Changed context"
    sibling.source_hash = "changed-source-hash"
    write_jsonl(project / "derived/units.jsonl", units)
    if not translated:
        write_jsonl(project / "translations/current.jsonl", [])
    approve(project, "all")
    report = run_qa(project, batch.batch_id)
    assert any(e.unit_id == sibling.unit_id and e.code == ("source-hash-mismatch" if translated else "missing-translation") for e in report.errors)
    refreshed = refresh_batch(project, batch.batch_id)
    assert sibling.unit_id not in refreshed.read_only_unit_ids
    assert sibling.unit_id in refreshed.translatable_unit_ids


@pytest.mark.parametrize("pages", [{}, {"other": []}, {"IMAGE": {}}, {"IMAGE": None}])
def test_layout_scan_requires_page_list(tmp_path, pages):
    image = "evidence/pages/fidelity-p0001.png"
    key = str((tmp_path / image).resolve())
    folder = tmp_path / "derived/fidelity-layout"
    write_json(folder / "000-partial.json", {"status": "ok", "fingerprint": "wanted", "pages": {key if k == "IMAGE" else k: v for k, v in pages.items()}})
    valid = {"status": "ok", "fingerprint": "wanted", "pages": {key: []}}
    write_json(folder / "zzz-valid.json", valid)
    assert fidelity._cached_layout(tmp_path, {"layout_status": "ok", "layout_fingerprint": "wanted", "page_image": image}) == valid
