import subprocess
from pathlib import Path

import pytest

from littrans import layout_detector, layout_worker
from littrans.rendering import _inline_html
from littrans.storage import read_json, write_json


@pytest.mark.parametrize("fence", ["~~~", "~~~~", "```"])
def test_fenced_html_footnotes_remain_literal(fence):
    text = f"Real[^1]\n{fence}text\nLiteral[^1]\n{fence}"
    result = _inline_html(text, "source", {"1": "note"})
    assert result.count('class="footnote-ref"') == 1
    assert "Literal[^1]" in result


@pytest.mark.parametrize("cached", ["{", "[]", '{"status":"ok"}'])
def test_unreadable_layout_cache_retries(tmp_path, monkeypatch, cached):
    python = tmp_path / "python.exe"
    python.touch()
    model = tmp_path / "model"
    model.mkdir()
    monkeypatch.setattr(layout_detector, "runtime_paths", lambda: (python, model))
    monkeypatch.setattr(layout_detector, "_runtime_identity", lambda _: {"python": "test"})
    store = tmp_path / "layout"
    executions = []
    def run(command, **kwargs):
        executions.append(command)
        request = read_json(Path(command[2]))
        write_json(Path(command[3]), {"status": "ok", "pages": {}, "fingerprint": request["fingerprint"]})
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(layout_detector.subprocess, "run", run)
    output = Path(layout_detector.detect_layout([], store)["path"])
    output.write_text(cached, encoding="utf-8")
    assert layout_detector.detect_layout([], store)["status"] == "ok"
    assert layout_detector.detect_layout([], store)["status"] == "ok"
    assert len(executions) == 2


@pytest.mark.parametrize("failure", [KeyboardInterrupt, OSError])
def test_atomic_worker_failure_preserves_cache(tmp_path, monkeypatch, failure):
    output = tmp_path / "layout.json"
    output.write_text('{"old":true}', encoding="utf-8")
    before = output.read_bytes()
    def fail(source, destination):
        assert read_json(Path(source)) == {"new": True}
        assert Path(destination) == output
        raise failure
    monkeypatch.setattr(layout_worker.os, "replace", fail)
    with pytest.raises(failure):
        layout_worker._write_result(output, {"new": True})
    assert output.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["layout.json"]


def test_atomic_worker_success(tmp_path):
    output = tmp_path / "layout.json"
    layout_worker._write_result(output, {"pages": {"image": []}})
    assert read_json(output) == {"pages": {"image": []}}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["layout.json"]
