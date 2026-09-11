from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import fitz
import pytest
from typer.testing import CliRunner

from littrans import cli, layout_runtime
from littrans.fidelity import prepare_source
from littrans.models import ProjectConfig
from littrans.storage import initialize_project_dirs, save_project, sha256_file

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((60, 80), "Let x = 1. Then the result holds.")
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="test", title="test", source_path="source/book.pdf",
                                         source_sha256=sha256_file(pdf), source_pages=1,
                                         profile="technical-book"))
    return tmp_path


def _unavailable(images: list[Path], output: Path) -> dict[str, Any]:
    return {"status": "unavailable", "reason": "layout interpreter missing", "pages": {}}


def test_prepare_refuses_without_layout_runtime_by_default(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.fidelity as fidelity

    monkeypatch.setattr(fidelity, "detect_layout", _unavailable)
    with pytest.raises(ValueError, match="Layout runtime unavailable.*layout install.*--allow-missing-layout"):
        prepare_source(project)
    assert not (project / "derived/fidelity-pages/p0001.json").exists()
    result = prepare_source(project, allow_missing_layout=True)
    assert result["layout_status"] == "unavailable"
    assert result["prepared_pages"] == [1]


def test_cli_prepare_forwards_allow_missing_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.fidelity as fidelity

    observed: list[tuple[Any, ...]] = []

    def prepare(*args: Any) -> dict[str, Any]:
        observed.append(args)
        return {"prepared_pages": [1]}

    monkeypatch.setattr(fidelity, "prepare_source", prepare)
    result = runner.invoke(cli.app, ["source", "prepare", str(tmp_path), "--allow-missing-layout"])
    assert result.exit_code == 0, result.output
    assert observed == [(tmp_path, "all", False, True)]


def test_layout_status_reports_missing_interpreter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(layout_runtime, "runtime_paths", lambda: (None, None))
    status = layout_runtime.layout_runtime_status()
    assert status == {"python": None, "model": None, "mineru_version": None, "ok": False,
                      "reason": "layout interpreter missing", "install_command": "littrans layout install"}
    monkeypatch.setattr(layout_runtime, "runtime_paths", lambda: (Path(__file__), tmp_path))
    monkeypatch.setattr(layout_runtime, "_run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "3.4.4\n", ""))
    assert layout_runtime.layout_runtime_status()["reason"] == "expected mineru==3.4.5, found 3.4.4"
    monkeypatch.setattr(layout_runtime, "_run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "3.4.5\n", ""))
    assert layout_runtime.layout_runtime_status()["reason"] == "PP-DocLayoutV2 weights missing"
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "model.safetensors").write_bytes(b"")
    assert layout_runtime.layout_runtime_status()["ok"] is True


def test_doctor_includes_layout_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(layout_runtime, "runtime_paths", lambda: (None, None))
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["layout_runtime"]["ok"] is False
    assert payload["layout_runtime"]["install_command"] == "littrans layout install"
    status = runner.invoke(cli.app, ["layout", "status"])
    assert json.loads(status.output)["reason"] == "layout interpreter missing"


def test_select_base_python_rejects_unsupported_versions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    versions = {"py313": (3, 13), "py314": (3, 14), "broken": None}
    monkeypatch.setattr(layout_runtime, "_interpreter_version", lambda python: versions[python.name])
    assert layout_runtime.select_base_python(Path("py313")) == Path("py313")
    with pytest.raises(RuntimeError, match="No Python 3.10-3.13 interpreter"):
        layout_runtime.select_base_python(Path("py314"))
    monkeypatch.setenv("LITTRANS_LAYOUT_BASE_PYTHON", "broken")
    with pytest.raises(RuntimeError, match="LITTRANS_LAYOUT_BASE_PYTHON"):
        layout_runtime.select_base_python()


def test_install_is_idempotent_and_refuses_external_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(layout_runtime, "layout_runtime_status", lambda: {"ok": True, "python": "p", "model": "m"})
    assert layout_runtime.install_layout_runtime()["installed"] is False
    monkeypatch.setenv("LITTRANS_LAYOUT_PYTHON", "elsewhere")
    with pytest.raises(RuntimeError, match="externally managed"):
        layout_runtime.install_layout_runtime()
    with pytest.raises(ValueError, match="huggingface or modelscope"):
        layout_runtime.install_layout_runtime(model_source="gitlab")


def test_review_import_reuses_cached_layout_detections(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A units/regions correction must re-prepare with the page's recorded detector output."""
    import littrans.fidelity as fidelity
    from littrans.fidelity import build_source_review_packet, import_source_review
    from littrans.storage import read_json, write_json

    calls: list[list[Path]] = []

    def fake_detect(images: list[Path], output: Path) -> dict[str, Any]:
        calls.append(images)
        payload = {"status": "ok", "fingerprint": "fake-fingerprint", "pages": {
            str(images[0].resolve()): [{"label": "title", "bbox": [100, 140, 700, 200], "score": 0.9}],
        }}
        write_json(output, payload)
        return payload

    monkeypatch.setattr(fidelity, "detect_layout", fake_detect)
    prepare_source(project)
    ledger = read_json(project / "derived/fidelity-pages/p0001.json")
    assert ledger["layout_status"] == "ok" and ledger["layout_fingerprint"] == "fake-fingerprint"
    heading_kinds = [u["kind"] for u in read_json(Path(build_source_review_packet(project, "1")["packet_path"]))["pages"][0]["units"]]
    assert "heading" in heading_kinds

    packet = build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "layout-reuse-test"
    page = read_json(Path(packet["packet_path"]))["pages"][0]
    review["pages"][0]["override"] = {"regions": [{"preserve_asset_id": a["id"]} for a in page["assets"]]}
    review_path = project / "tmp" / "review.json"
    write_json(review_path, review)
    assert import_source_review(project, review_path, confirm_visual_review=True)["changed_pages"] == [1]
    ledger = read_json(project / "derived/fidelity-pages/p0001.json")
    assert ledger["layout_fingerprint"] == "fake-fingerprint"
    kinds = [u["kind"] for u in read_json(Path(build_source_review_packet(project, "1")["packet_path"]))["pages"][0]["units"]]
    assert "heading" in kinds
    assert len(calls) == 1  # the correction reused the cached result instead of re-running the detector
