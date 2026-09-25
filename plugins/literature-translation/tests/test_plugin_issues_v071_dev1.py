"""0.7.1-dev.1: LT-088..LT-091 from the 2026-09-25 trial of 0.7.0-dev.1.

The layout runtime lives outside AppData and proves its identity (LT-088); `source prepare
--replace` names every receipt it removed (LT-089) and keeps the guidance record a receipt
was reviewed under (LT-090); MuPDF warnings stay off stdout (LT-091). Synthetic pages and
stubbed runtimes only.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from fidelity_fixtures import layout_probe_stdout
from test_plugin_issues_ch1_round12 import _approve, _profile, _two_page_project, _write_profile
from typer.testing import CliRunner

from littrans import cli, layout_detector, layout_runtime
from littrans.fidelity import prepare_source, verify_fidelity
from littrans.storage import read_json
from littrans.structure_profile import probe_structure

VENV_PYTHON = "Scripts/python.exe" if os.name == "nt" else "bin/python"

# ---------------------------------------------------------------------------------------
# LT-088: runtime identity, redirection, repair and the legacy AppData cache.
# ---------------------------------------------------------------------------------------


@pytest.fixture
def external_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A detector interpreter outside the managed cache (no ready receipt applies) and weights."""
    python = tmp_path / "ext/venv" / VENV_PYTHON
    python.parent.mkdir(parents=True)
    python.touch()
    (tmp_path / "ext/venv/pyvenv.cfg").write_text("home = C:\\opt\\python\\3.13\nversion = 3.13.14\n", encoding="utf-8")
    model = tmp_path / "weights"
    model.mkdir()
    for name in ("config.json", "model.safetensors"):
        (model / name).touch()
    monkeypatch.setattr(layout_runtime, "runtime_paths", lambda: (python, model))
    return python, model


def _probe(monkeypatch: pytest.MonkeyPatch, stdout: str, returncode: int = 0, stderr: str = "") -> None:
    monkeypatch.setattr(layout_runtime, "_run", lambda command, **kwargs: subprocess.CompletedProcess(command, returncode, stdout, stderr))


def test_status_proves_the_interpreter_identity_and_its_imports(external_runtime: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _probe(monkeypatch, layout_probe_stdout(version="3.13.14"))
    status = layout_runtime.layout_runtime_status()
    assert status["ok"], status
    assert status["identity"]["pyvenv_version"] == "3.13.14" and status["identity"]["version"] == "3.13.14"
    assert status["identity"]["pyvenv_home"] == "C:\\opt\\python\\3.13"
    # The Codex view: the same path runs a 3.12 interpreter that cannot import torch.
    _probe(monkeypatch, layout_probe_stdout(version="3.12.14", import_error="ImportError: cannot import name 'is_fake_tensor'"))
    status = layout_runtime.layout_runtime_status()
    assert not status["ok"] and status["reason"].startswith("environment identity mismatch: pyvenv.cfg records Python 3.13.14")
    assert "layout install --repair" in status["reason"]
    _probe(monkeypatch, layout_probe_stdout(version="3.13.14", import_error="OSError: [WinError 126] torch_python.dll"))
    status = layout_runtime.layout_runtime_status()
    assert not status["ok"] and status["reason"].startswith("detector stack does not import (OSError: [WinError 126]")
    _probe(monkeypatch, "", returncode=3, stderr="Fatal Python error")
    status = layout_runtime.layout_runtime_status()
    assert not status["ok"] and "layout interpreter does not run (exit 3): Fatal Python error" in status["reason"]


def test_a_redirected_view_is_refused(external_runtime: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    private = tmp_path / "Packages" / "OpenAI.Codex_2p2nqsd0c76g0" / "LocalCache" / "Local"
    assert layout_detector.redirected(tmp_path / "anything") is False
    assert layout_detector.redirected(private / "littrans") is False  # named directly: not redirected
    real_resolve = Path.resolve

    def resolve(self: Path, strict: bool = False) -> Path:
        if "littrans-real" in self.parts:
            return private.joinpath(*self.parts[self.parts.index("littrans-real"):])
        return real_resolve(self, strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert layout_detector.redirected(tmp_path / "littrans-real" / "python.exe")
    _probe(monkeypatch, layout_probe_stdout(version="3.13.14", resolved_executable=str(tmp_path / "littrans-real" / "python.exe")))
    status = layout_runtime.layout_runtime_status()
    assert not status["ok"] and status["reason"] == layout_detector.REDIRECTED_REASON


def test_install_refuses_appdata_in_a_packaged_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LITTRANS_LAYOUT_PYTHON")
    monkeypatch.delenv("LITTRANS_LAYOUT_MODEL")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(layout_runtime, "layout_cache_root", lambda: tmp_path / "local/littrans/layout")
    monkeypatch.setattr(layout_runtime, "packaged_app", lambda: True)
    with pytest.raises(RuntimeError, match="AppData redirection"):
        layout_runtime.install_layout_runtime()
    with pytest.raises(ValueError, match="not both"):
        layout_runtime.install_layout_runtime(force=True, repair=True)


def _managed_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, list[str]]:
    """A managed cache whose installer steps are stubbed; returns (environment, model, calls)."""
    monkeypatch.delenv("LITTRANS_LAYOUT_PYTHON")
    monkeypatch.delenv("LITTRANS_LAYOUT_MODEL")
    cache = tmp_path / "home/.littrans/layout"
    environment = cache / "venv"
    model = cache / layout_runtime.MODEL_NAME
    calls: list[str] = []
    monkeypatch.setattr(layout_runtime, "layout_cache_root", lambda: cache)
    monkeypatch.setattr(layout_runtime, "layout_runtime_status", lambda: {"ok": (environment / layout_runtime.READY_MARKER).exists()})
    monkeypatch.setattr(layout_runtime, "_pip", lambda *args: calls.append("pip"))
    monkeypatch.setattr(layout_runtime, "select_base_python", lambda python=None: Path("base-python"))

    def download(python: Path, destination: Path, source: str) -> None:
        calls.append("download")
        destination.mkdir(parents=True, exist_ok=True)
        for name in ("config.json", "model.safetensors"):
            (destination / name).write_text("downloaded", encoding="utf-8")

    def create_venv(command: list[str], check: bool = False, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append("venv")
        (environment / VENV_PYTHON).parent.mkdir(parents=True, exist_ok=True)
        (environment / VENV_PYTHON).touch()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(layout_runtime, "_download_weights", download)
    monkeypatch.setattr(layout_runtime.subprocess, "run", create_venv)
    monkeypatch.setattr(layout_runtime, "_smoke_test", lambda *args: {"status": "ok"})
    return environment, model, calls


def test_repair_recreates_the_environment_and_keeps_verified_weights(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    environment, model, calls = _managed_cache(tmp_path, monkeypatch)
    (environment / VENV_PYTHON).parent.mkdir(parents=True)
    (environment / VENV_PYTHON).touch()
    (environment / "Lib/site-packages/torch").mkdir(parents=True)
    (environment / "Lib/site-packages/torch/_C.cp312-win_amd64.pyd").touch()
    model.mkdir(parents=True)
    for name in ("config.json", "model.safetensors"):
        (model / name).write_text("verified", encoding="utf-8")
    layout_detector.write_ready_marker(environment / layout_runtime.READY_MARKER, model)
    result = layout_runtime.install_layout_runtime(repair=True)
    assert result["installed"] and result["repaired"] and not result["weights_downloaded"]
    assert calls == ["venv", "pip", "pip", "pip"]
    assert not (environment / "Lib/site-packages/torch").exists()  # the mixed environment is gone
    assert (model / "model.safetensors").read_text(encoding="utf-8") == "verified"
    assert layout_detector.ready_weight_hashes(environment / layout_runtime.READY_MARKER) == layout_detector.model_weight_hashes(model)


def test_install_adopts_the_legacy_appdata_weights_its_receipt_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    environment, model, calls = _managed_cache(tmp_path, monkeypatch)
    legacy = tmp_path / "local/littrans"
    old_model = legacy / "layout" / layout_runtime.MODEL_NAME
    old_model.mkdir(parents=True)
    for name in ("config.json", "model.safetensors"):
        (old_model / name).write_text("legacy", encoding="utf-8")
    layout_detector.write_ready_marker(legacy / "layout/venv" / layout_runtime.READY_MARKER, old_model)
    monkeypatch.setattr(layout_runtime, "legacy_cache_root", lambda: legacy)
    result = layout_runtime.install_layout_runtime()
    assert result["adopted_legacy_weights"] == str(old_model) and not result["weights_downloaded"]
    assert "download" not in calls
    assert (model / "model.safetensors").read_text(encoding="utf-8") == "legacy"
    assert old_model.is_dir()  # the legacy cache is never removed
    # Weights the legacy receipt does not verify are downloaded instead.
    (old_model / "model.safetensors").write_text("truncated", encoding="utf-8")
    environment.joinpath(layout_runtime.READY_MARKER).unlink()
    for path in model.iterdir():
        path.unlink()
    model.rmdir()
    calls.clear()
    result = layout_runtime.install_layout_runtime()
    assert result["adopted_legacy_weights"] is None and result["weights_downloaded"] and "download" in calls


def test_status_names_the_legacy_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "local/littrans/layout").mkdir(parents=True)
    monkeypatch.setattr(layout_runtime, "legacy_cache_root", lambda: tmp_path / "local/littrans")
    monkeypatch.setattr(layout_runtime, "runtime_paths", lambda: (None, None))
    status = layout_runtime.layout_runtime_status()
    assert status["legacy_cache"] == str(tmp_path / "local/littrans") and status["cache_root"] == os.environ["LITTRANS_CACHE_DIR"]


# ---------------------------------------------------------------------------------------
# LT-089 / LT-090: what `source prepare --replace` does to a page's receipt.
# ---------------------------------------------------------------------------------------


@pytest.fixture
def reviewed_after_profile_edit(tmp_path: Path) -> Path:
    """Page 1 prepared under one guidance, then reviewed and approved under the current one.

    The pilot's pages 26-28: the ledger's guidance digest is older than the packet the
    receipt was reviewed from, and `source verify` compares guidance by content.
    """
    root = _two_page_project(tmp_path)
    probe_structure(root, "1")
    profile = _profile(root)
    profile.update(handling_rules={"lists": "Labels are printed (a), (b)."}, review_notes="Inspected page 1.",
                   inspected_pages=[1], status="reviewed")
    _write_profile(root, profile)
    prepare_source(root, "1", allow_missing_layout=True)
    profile["handling_rules"]["lists"] = "Labels are printed (a), (b), set upright."
    _write_profile(root, profile)
    _approve(root, "1")
    assert verify_fidelity(root, "1")["passed"]
    return root


def test_replace_keeps_a_receipt_reviewed_under_the_current_guidance(reviewed_after_profile_edit: Path) -> None:
    root = reviewed_after_profile_edit
    before = read_json(root / "derived/fidelity-pages/p0001.json")["structure"]["document_profile"]
    result = prepare_source(root, "1", replace=True, allow_missing_layout=True)
    assert result["retained_receipt_pages"] == [1] and result["invalidated_pages"] == []
    assert read_json(root / "derived/fidelity-pages/p0001.json")["structure"]["document_profile"] == before
    assert verify_fidelity(root, "1")["passed"]


def test_replace_lists_the_pages_of_the_run_whose_receipts_it_removed(reviewed_after_profile_edit: Path) -> None:
    root = reviewed_after_profile_edit
    profile = _profile(root)
    profile["handling_rules"]["lists"] = "Labels are printed 1., 2."
    _write_profile(root, profile)
    assert not verify_fidelity(root, "1")["passed"]
    result = prepare_source(root, "1", replace=True, allow_missing_layout=True)
    assert result["prepared_pages"] == [1]
    assert result["retained_receipt_pages"] == [] and result["invalidated_pages"] == [1]
    assert not (root / "evidence/pages/fidelity-p0001.review.json").is_file()


# ---------------------------------------------------------------------------------------
# LT-091: MuPDF warnings leave stdout to the JSON.
# ---------------------------------------------------------------------------------------

DANGLING_USE = (b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                b'width="10" height="10"><use xlink:href="#nope"/></svg>')


def test_mupdf_warnings_reach_stderr_not_stdout(capfd: pytest.CaptureFixture[str]) -> None:
    pymupdf.TOOLS.mupdf_warnings()  # drop anything earlier tests stored
    with pymupdf.open(stream=DANGLING_USE, filetype="svg"):
        pass
    cli.emit({"ok": True})
    captured = capfd.readouterr()
    assert json.loads(captured.out) == {"ok": True}
    assert "LitTrans MuPDF warning: svg: cannot find linked symbol" in captured.err
    cli.emit({"ok": True})
    assert "MuPDF" not in capfd.readouterr().err  # relayed once


def test_layout_install_cli_forwards_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[tuple[Any, ...]] = []
    monkeypatch.setattr(layout_runtime, "install_layout_runtime", lambda *args: observed.append(args) or {"ok": True})
    result = CliRunner().invoke(cli.app, ["layout", "install", "--repair"])
    assert result.exit_code == 0, result.output
    assert observed == [(None, False, "huggingface", True)]
