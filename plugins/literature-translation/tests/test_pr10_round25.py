import pytest
from test_efficiency_v4 import _submit
from test_fidelity_source import approve
from test_footnotes_v6 import _linked_notes
from test_pr10_round8 import submit
from test_workflow_v6 import project as workflow_project

from littrans import fidelity
from littrans.context_packets import original_context
from littrans.fidelity_models import load_assets
from littrans.models import SourceUnit, TranslationRecord
from littrans.quality import current_qa_context_fingerprint, qa_report_is_current, run_qa
from littrans.rendering import _inline_html
from littrans.semantics import explicit_footnote_calls
from littrans.storage import read_jsonl, write_jsonl
from littrans.workflow import workflow_status

project = workflow_project


@pytest.mark.parametrize("text", ["Prices are $5 [^1] and $10", "Prices are $5.00 [^1] and $10.50",
                                  "Prices are $5 [^1] and $10; formula $x$", "$x$ costs $5 [^1] or $10"])
def test_currency_does_not_hide_footnotes(text):
    assert explicit_footnote_calls(text) == ["1"]
    assert _inline_html(text).count('class="footnote-ref"') == 1


@pytest.mark.parametrize("formula", ["$x[^1]$", "$5 + x[^1]$", "$$\nx[^1]\n$$", r"\(x[^1]\)"])
def test_real_math_still_protects_literal_footnotes(formula):
    assert explicit_footnote_calls(formula) == []
    assert 'class="footnote-ref"' not in _inline_html(formula)


def test_currency_source_can_be_verified_and_omission_fails_qa(tmp_path):
    root, batches, units = _linked_notes(tmp_path)
    units[0].source_text = units[0].source_markdown = "Prices are $5 [^1] and $10"
    write_jsonl(root / "derived/units.jsonl", units)
    approve(root, "all")
    _submit(root, batches[1].batch_id, target_text="注释")
    _submit(root, batches[0].batch_id, target_text="价格为 $5 和 $10")
    assert "footnote-call-mismatch" in {e.code for e in run_qa(root, batches[0].batch_id).errors}


@pytest.mark.parametrize("dependency", [False, True])
def test_changed_image_receipt_invalidates_cached_qa(project, dependency):
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    if dependency:
        units[0].continued_to_next = units[1].continues_from_previous = True
        write_jsonl(project / "derived/units.jsonl", units)
        approve(project, "all")
    for bid in ("sample-one-b001", "sample-two-b001"):
        submit(project, bid)
    bid = "sample-one-b001"
    assert run_qa(project, bid).passed


    before = current_qa_context_fingerprint(project, bid)
    records = read_jsonl(project / "translations/current.jsonl", TranslationRecord)
    records[int(dependency)].image_evidence = {}
    write_jsonl(project / "translations/current.jsonl", records)
    assert current_qa_context_fingerprint(project, bid) != before
    assert not qa_report_is_current(project, bid)
    report = run_qa(project, bid)
    assert "asset-image-receipt-missing" in {e.code for e in report.errors}


@pytest.mark.parametrize("image", ["canvas", "crop"])
def test_missing_image_still_routes_to_source_repair(project, image):
    bid = "sample-one-b001"
    submit(project, bid)
    assert run_qa(project, bid).passed
    path = (project / "evidence/pages/fidelity-p0001.png" if image == "canvas" else
            project / next(iter(load_assets(project).values())).fragments[0].png_path)
    path.unlink()
    assert workflow_status(project, [bid])["stage"] == "source-review"


def test_republished_canvas_invalidates_translation_image_binding(project, monkeypatch):
    bid = "sample-one-b001"
    submit(project, bid)
    assert run_qa(project, bid).passed
    before = current_qa_context_fingerprint(project, bid)
    source_before = {u.unit_id: u.source_hash for u in read_jsonl(project / "derived/units.jsonl", SourceUnit)}
    canvas = project / "evidence/pages/fidelity-p0001.png"
    original = fidelity.atomic_write_bytes
    def changed(path, content):
        original(path, content + b"changed-encoder" if path == canvas else content)
    monkeypatch.setattr(fidelity, "atomic_write_bytes", changed)
    fidelity.prepare_source(project, "1", replace=True, allow_missing_layout=True)
    approve(project, "all")
    assert {u.unit_id: u.source_hash for u in read_jsonl(project / "derived/units.jsonl", SourceUnit)} == source_before
    assert current_qa_context_fingerprint(project, bid) != before
    assert workflow_status(project, [bid])["stage"] == "qa"
    assert "asset-image-receipt-missing" in {e.code for e in run_qa(project, bid).errors}
    records = read_jsonl(project / "translations/current.jsonl", TranslationRecord)
    unit_map = {u.unit_id: u for u in read_jsonl(project / "derived/units.jsonl", SourceUnit)}
    for record in records:
        record.image_evidence = original_context(project, [unit_map[record.unit_id]])["required_images"]
    write_jsonl(project / "translations/current.jsonl", records)
    assert run_qa(project, bid).passed
