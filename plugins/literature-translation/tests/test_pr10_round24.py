import os

import pytest
from test_efficiency_v4 import _submit
from test_footnotes_v6 import _linked_notes

from littrans import layout_runtime
from littrans.models import SourceUnit
from littrans.quality import run_qa
from littrans.rendering import _inline_html, _markdown_footnote_calls
from littrans.semantics import explicit_footnote_calls
from littrans.source_structure import _bold_run_in


@pytest.mark.parametrize("fence", ["```", "~~~", "````", "~~~~"])
@pytest.mark.parametrize("invalid_close", ["middle", "suffix"])
@pytest.mark.parametrize("closed", [False, True])
def test_fence_boundaries_are_shared(fence, invalid_close, closed):
    invalid = "example " + fence + " inline" if invalid_close == "middle" else fence + " trailing-text"
    text = f"   {fence}python\n{invalid}\n[^1]\n"
    if closed:
        text += "   " + fence + fence[0] + "  \nOutside[^2]"
    assert explicit_footnote_calls(text) == (["2"] if closed else [])
    assert _inline_html(text).count('class="footnote-ref"') == int(closed)
    note = SourceUnit(unit_id="note", kind="footnote", page=1, bbox=(0, 0, 1, 1), source_text="Note", source_hash="n", footnote_number="2", confidence=1)
    unit = SourceUnit(unit_id="body", kind="paragraph", page=1, bbox=(0, 0, 1, 1), source_text="Body", source_hash="b", footnote_refs=["note"], confidence=1)
    rewritten = _markdown_footnote_calls(text, unit, {"note": note})
    assert "[^1]" in rewritten
    assert ("Outside[^2]" not in rewritten) if closed else rewritten == text


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_literal_call_cannot_replace_source_call_in_qa(tmp_path, fence):
    root, batches, _ = _linked_notes(tmp_path)
    _submit(root, batches[1].batch_id, target_text="注释")
    _submit(root, batches[0].batch_id, target_text=f"译文\n{fence}\nexample {fence} inline\n[^1]\n{fence}")
    assert "footnote-call-mismatch" in {e.code for e in run_qa(root, batches[0].batch_id).errors}


@pytest.mark.parametrize("failure", ["copy", "smoke"])
def test_incomplete_model_retry_redownloads(tmp_path, monkeypatch, failure):
    monkeypatch.delenv("LITTRANS_LAYOUT_PYTHON", raising=False)
    monkeypatch.delenv("LITTRANS_LAYOUT_MODEL", raising=False)
    cache = tmp_path / "cache"
    environment = cache / "venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    model = cache / layout_runtime.MODEL_NAME
    model.mkdir()
    for name in ("config.json", "model.safetensors"):
        (model / name).touch()
    monkeypatch.setattr(layout_runtime, "layout_cache_root", lambda: cache)
    monkeypatch.setattr(layout_runtime, "layout_runtime_status", lambda: {"ok": (environment / layout_runtime.READY_MARKER).exists()})
    monkeypatch.setattr(layout_runtime, "_pip", lambda *args: None)
    downloads = []
    def download(*args):
        downloads.append(1)
        if len(downloads) == 1:
            if failure == "copy":
                raise RuntimeError("copy interrupted")
        else:
            (model / "remaining-snapshot-file").touch()
    def smoke(*args):
        if not (model / "remaining-snapshot-file").exists():
            raise RuntimeError("incomplete snapshot")
        return {"status": "ok"}
    monkeypatch.setattr(layout_runtime, "_download_weights", download)
    monkeypatch.setattr(layout_runtime, "_smoke_test", smoke)
    with pytest.raises(RuntimeError):
        layout_runtime.install_layout_runtime()
    assert not (environment / layout_runtime.READY_MARKER).exists()
    assert layout_runtime.install_layout_runtime()["ok"]
    assert len(downloads) == 2


@pytest.mark.parametrize("whitespace", [" ", "\t", "\r"])
def test_bold_run_in_after_whitespace(whitespace):
    glyphs = [{"text": char, "font": "Bold"} for char in "Example."] + [{"text": "body", "font": "Regular"}]
    assert _bold_run_in(glyphs, [{"text": whitespace, "font": "Regular"}])
    assert _bold_run_in(glyphs, [])
    assert not _bold_run_in(glyphs, [{"text": "Previous", "font": "Bold"}])
