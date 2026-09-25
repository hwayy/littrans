import os
import subprocess
from pathlib import Path

import pymupdf as fitz
import pytest
from fidelity_fixtures import confirm_structure_checks, layout_probe_stdout
from test_fidelity_source import approve

from littrans import fidelity, layout_detector, layout_runtime
from littrans.batching import create_batches, load_manifest
from littrans.context_packets import original_context
from littrans.models import SourceUnit, TranslationRecord
from littrans.project import initialize_project
from littrans.quality import current_qa_context_fingerprint, run_qa
from littrans.rendering import _inline_html
from littrans.storage import read_json, read_jsonl, write_json, write_jsonl
from littrans.translation import submit_translation
from littrans.workflow import workflow_status


@pytest.fixture
def prose_project(tmp_path, monkeypatch):
    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {
        "status": "unavailable", "reason": "Prose-only oracle", "pages": {},
    })
    source = tmp_path / "prose.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 60), "The identity holds.")
        doc.save(source)
    root = tmp_path / "prose-project"
    initialize_project(source, root, "technical-book")
    fidelity.prepare_source(root, allow_missing_layout=True)
    packet = fidelity.build_source_review_packet(root)
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "prose-only-oracle"
    for decision in review["pages"]:
        for key in ("viewed_original", "coverage_complete", "boundaries_complete",
                    "reading_order_correct", "grouping_checked", "layout_fallback_checked"):
            decision[key] = True
        confirm_structure_checks(decision)
    write_json(root / "prose-review.json", review)
    fidelity.import_source_review(root, root / "prose-review.json", True)
    create_batches(root, "1", prefix="prose")
    units = [unit for unit in read_jsonl(root / "derived/units.jsonl", SourceUnit) if unit.translatable]
    assert units
    assert all("{{asset:" not in (unit.source_markdown or unit.source_text) for unit in units)
    return root


def _submit_prose(root, bid, image_evidence=None):
    manifest = load_manifest(root, bid)
    units = {unit.unit_id: unit for unit in read_jsonl(root / "derived/units.jsonl", SourceUnit)}
    records = []
    for unit_id in manifest.translatable_unit_ids:
        unit = units[unit_id]
        receipt = original_context(root, [unit])["required_images"] if image_evidence is None else image_evidence
        records.append(TranslationRecord(
            unit_id=unit.unit_id, source_hash=unit.source_hash,
            target_text=unit.source_text.replace("The identity holds.", "该恒等式成立。"),
            image_evidence=receipt,
        ))
    path = root / "prose-translations.jsonl"
    write_jsonl(path, records)
    return submit_translation(root, bid, path)


def test_prose_units_require_page_canvas_receipts(prose_project):
    bid = "prose-b001"
    with pytest.raises(ValueError, match="Missing/stale original-image"):
        _submit_prose(prose_project, bid, image_evidence={})
    _submit_prose(prose_project, bid)
    assert run_qa(prose_project, bid).passed
    records = read_jsonl(prose_project / "translations/current.jsonl", TranslationRecord)
    records[0].image_evidence = {}
    write_jsonl(prose_project / "translations/current.jsonl", records)
    assert "asset-image-receipt-missing" in {item.code for item in run_qa(prose_project, bid).errors}


def test_republished_prose_canvas_requires_rebind(prose_project, monkeypatch):
    bid = "prose-b001"
    _submit_prose(prose_project, bid)
    assert run_qa(prose_project, bid).passed
    before = current_qa_context_fingerprint(prose_project, bid)
    canvas = prose_project / "evidence/pages/fidelity-p0001.png"
    original = fidelity.atomic_write_bytes

    def changed(path, content):
        original(path, content + b"changed-encoder" if path == canvas else content)

    monkeypatch.setattr(fidelity, "atomic_write_bytes", changed)
    fidelity.prepare_source(prose_project, "1", replace=True, allow_missing_layout=True)
    approve(prose_project, "all")
    assert current_qa_context_fingerprint(prose_project, bid) != before
    assert workflow_status(prose_project, [bid])["stage"] == "qa"
    assert "asset-image-receipt-missing" in {item.code for item in run_qa(prose_project, bid).errors}
    units = {unit.unit_id: unit for unit in read_jsonl(prose_project / "derived/units.jsonl", SourceUnit)}
    stale = [
        record.model_copy(update={"source_hash": units[record.unit_id].source_hash})
        for record in read_jsonl(prose_project / "translations/current.jsonl", TranslationRecord)
    ]
    write_jsonl(prose_project / "stale.jsonl", stale)
    with pytest.raises(ValueError, match="Missing/stale original-image"):
        submit_translation(prose_project, bid, prose_project / "stale.jsonl")
    for record in stale:
        record.image_evidence = original_context(prose_project, [units[record.unit_id]])["required_images"]
    write_jsonl(prose_project / "fresh.jsonl", stale)
    submit_translation(prose_project, bid, prose_project / "fresh.jsonl")
    assert run_qa(prose_project, bid).passed


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_fenced_html_preserves_line_breaks(fence):
    text = f"Example\n{fence}python\ndef nest():\n    return {{\n        'ok': True\n    }}\n{fence}\nDone"
    rendered = _inline_html(text)
    assert "<pre><code>" in rendered
    assert "def nest():\n    return {" in rendered
    assert "\n        " in rendered
    assert "def nest(): return" not in rendered
    assert rendered.count("<code>") == 1


def test_truncated_managed_weights_redownload(tmp_path, monkeypatch):
    monkeypatch.delenv("LITTRANS_LAYOUT_PYTHON", raising=False)
    monkeypatch.delenv("LITTRANS_LAYOUT_MODEL", raising=False)
    cache = tmp_path / "cache"
    environment = cache / "venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    model = cache / layout_runtime.MODEL_NAME
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"good-weights")
    layout_detector.write_ready_marker(environment / layout_runtime.READY_MARKER, model)
    monkeypatch.setattr(layout_runtime, "layout_cache_root", lambda: cache)
    monkeypatch.setattr(layout_runtime, "runtime_paths", lambda: (python, model))
    monkeypatch.setattr(
        layout_runtime, "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, layout_probe_stdout(), ""),
    )
    assert layout_runtime.layout_runtime_status()["ok"]
    (model / "model.safetensors").write_bytes(b"truncated")
    assert not layout_runtime.layout_runtime_status()["ok"]
    monkeypatch.setattr(layout_runtime, "_pip", lambda *args: None)
    downloads = []

    def download(*args):
        downloads.append(1)
        (model / "model.safetensors").write_bytes(b"good-weights")

    monkeypatch.setattr(layout_runtime, "_download_weights", download)
    monkeypatch.setattr(layout_runtime, "_smoke_test", lambda *args: {"status": "ok"})
    assert layout_runtime.install_layout_runtime()["ok"]
    assert downloads == [1]
    assert layout_detector.ready_weight_hashes(environment / layout_runtime.READY_MARKER) == (
        layout_detector.model_weight_hashes(model)
    )
