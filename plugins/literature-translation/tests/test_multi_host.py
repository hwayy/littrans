"""Several hosts, one record: nothing the record carries names the machine that wrote it.

A project synchronised through a plain git remote between a Windows and a Linux host
must resolve the plugin, find its PDF, replay its layout evidence and import its review
records on either side. These tests pin the pieces that used to embed a machine: the
launcher's recorded plugin root, ``project.yaml``'s source path, the layout cache root,
the detector result's image map, the layout evidence policy of the record and the Cursor
dry-run record's packet path.
"""
from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pymupdf
import pytest

from littrans import glyph_export, layout_detector
from littrans.fidelity import prepare_source, verify_fidelity
from littrans.project import initialize_project
from littrans.record import record_sets
from littrans.scaffold import scaffold_context, scaffold_project
from littrans.storage import read_json, sha256_file, write_json


def _pdf(path: Path, pages: int = 1) -> Path:
    document = pymupdf.open()
    for number in range(pages):
        document.new_page().insert_text((72, 72), f"Page {number + 1} of the fixture.")
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    return path


def _launcher_module(root: Path, **constants: str) -> ModuleType:
    launcher = root / "tools" / "lt.py"
    text = launcher.read_text(encoding="utf-8")
    for name, value in constants.items():
        start = text.index(f"{name} = ")
        text = text.replace(text[start:text.index("\n", start)], f"{name} = {value}")
    launcher.write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("lt_launcher_multi_host", launcher)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clear_host_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    from littrans.hosts import HOST_ENV_SIGNALS

    for names in HOST_ENV_SIGNALS.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)


def _plugin(directory: Path) -> Path:
    (directory / "scripts").mkdir(parents=True, exist_ok=True)
    (directory / "scripts" / "littrans.py").write_text("import sys; print(sys.argv[0])\n", encoding="utf-8")
    return directory


# --- launcher -----------------------------------------------------------------------------


def test_launcher_finds_the_plugin_under_another_hosts_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    initialize_project(_pdf(tmp_path / "book.pdf"), root, "technical-book", "Fixture")
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("LITTRANS_PLUGIN_ROOT", raising=False)
    _clear_host_signals(monkeypatch)
    # The root recorded on the generating host is gone here; the same path under this
    # user's home holds an upgraded version beside the recorded one.
    module = _launcher_module(
        root,
        RECORDED_PLUGIN_ROOT=repr(str(tmp_path / "elsewhere" / "cache" / "0.6.0-dev.8")),
        RECORDED_HOME_RELATIVE_ROOT='".claude/plugins/cache/littrans/literature-translation/0.6.0-dev.8"',
    )
    with pytest.raises(SystemExit, match="no literature-translation plugin found"):
        module.resolve_plugin_root()
    _plugin(home / ".claude/plugins/cache/littrans/literature-translation/0.6.0-dev.9")
    assert module.resolve_plugin_root() == home / ".claude/plugins/cache/littrans/literature-translation/0.6.0-dev.9"
    recorded = _plugin(home / ".claude/plugins/cache/littrans/literature-translation/0.6.0-dev.8")
    assert module.resolve_plugin_root() == recorded  # the recorded version wins while it exists


def test_launcher_scans_every_clients_cache_and_never_the_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    initialize_project(_pdf(tmp_path / "book.pdf"), root, "technical-book", "Fixture")
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("LITTRANS_PLUGIN_ROOT", raising=False)
    _clear_host_signals(monkeypatch)
    # A Windows path recorded on the other host is one relative component on POSIX; its
    # "parent" is the working directory, which must not be searched for siblings.
    module = _launcher_module(root, RECORDED_PLUGIN_ROOT='r"C:\\Users\\other\\.claude\\plugins\\cache\\littrans\\literature-translation\\0.6.0"',
                              RECORDED_HOME_RELATIVE_ROOT='""')
    monkeypatch.chdir(tmp_path)
    _plugin(tmp_path / "0.99.0")
    with pytest.raises(SystemExit):
        module.resolve_plugin_root()
    cursor = _plugin(home / ".cursor/plugins/local/literature-translation")
    assert module.resolve_plugin_root() == cursor
    codex = _plugin(home / ".codex/plugins/cache/littrans/literature-translation/0.7.0")
    assert module.resolve_plugin_root() == codex  # a versioned install outranks a local one
    claude = _plugin(home / ".claude/plugins/cache/littrans/literature-translation/0.6.0-dev.9")
    assert module.resolve_plugin_root() == codex  # the highest version wins, not client order
    monkeypatch.setenv("CLAUDECODE", "1")
    assert module.resolve_plugin_root() == claude  # the session's client wins (LT-078)
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setenv("LITTRANS_PLUGIN_ROOT", str(cursor))
    assert module.resolve_plugin_root() == cursor


def test_scaffold_records_the_home_relative_plugin_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.scaffold as scaffold

    home = tmp_path / "home"
    plugin = home / ".claude/plugins/cache/littrans/literature-translation/0.6.0-dev.9"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(scaffold, "plugin_root", lambda: plugin)
    context = scaffold_context(tmp_path, tmp_path)
    assert context["plugin_root_home_relative"] == ".claude/plugins/cache/littrans/literature-translation/0.6.0-dev.9"
    monkeypatch.setattr(scaffold, "plugin_root", lambda: tmp_path / "checkout")
    assert scaffold_context(tmp_path, tmp_path)["plugin_root_home_relative"] == ""


def test_scaffold_launchers_are_executable_and_keep_native_line_endings(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(_pdf(tmp_path / "book.pdf"), root, "technical-book", "Fixture")
    attributes = (root / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "*.cmd text eol=crlf" in attributes and "*.sh text eol=lf" in attributes and "*.py text eol=lf" in attributes
    if os.name != "nt":
        for name in ("lt.sh", "lt.py"):
            assert (root / "tools" / name).stat().st_mode & stat.S_IXUSR
    assert scaffold_project(root)["created"] == []


# --- paths the record carries -------------------------------------------------------------


def test_project_init_records_a_pdf_inside_the_project_relatively(tmp_path: Path) -> None:
    root = tmp_path / "project"
    config = initialize_project(_pdf(root / "source" / "book.pdf"), root, "technical-book", "Inside")
    assert config.source_path == "source/book.pdf"
    assert read_json(root / "derived" / "provenance.json")["source_path"] == "source/book.pdf"
    assert config.source(root) == (root / "source" / "book.pdf").resolve()
    outside = initialize_project(_pdf(tmp_path / "elsewhere.pdf"), tmp_path / "other", "technical-book", "Outside")
    assert Path(outside.source_path).is_absolute()


@pytest.mark.parametrize("system", ["nt", "posix"])
def test_layout_cache_root_follows_the_platform_convention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, system: str) -> None:
    # The platform is simulated on the module only: pathlib itself must keep this host's flavour.
    monkeypatch.setattr(layout_detector, "os", SimpleNamespace(name=system, environ=os.environ))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    expected = tmp_path / ("local" if system == "nt" else "xdg")
    assert layout_detector.layout_cache_root() == expected / "littrans/layout"
    monkeypatch.delenv("LOCALAPPDATA")
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert layout_detector.layout_cache_root() == tmp_path / "home" / ".cache" / "littrans/layout"


def test_dry_run_packet_paths_resolve_relative_to_the_project(tmp_path: Path) -> None:
    from littrans.external_review import _record_relative_path

    assert _record_relative_path(tmp_path, "reviews/external-dry-run/b/r/packet/review-packet.md") == tmp_path / "reviews/external-dry-run/b/r/packet/review-packet.md"
    absolute = tmp_path / "reviews" / "x.md"
    assert _record_relative_path(tmp_path, str(absolute)) == absolute


def test_exported_glyph_svgs_are_written_with_lf_bytes(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "book.pdf")
    with pymupdf.open(pdf) as document:
        page = document[0]
        glyphs = [g for g in _native_glyphs(page)][:3]
        result = glyph_export.export_owned_fragment(page, glyphs, tmp_path / "crop.svg", tmp_path / "crop.png", 150)
    assert result["dpi"] == 150
    data = (tmp_path / "crop.svg").read_bytes()
    assert b"\n" in data and b"\r\n" not in data


def _native_glyphs(page: pymupdf.Page) -> list[dict[str, Any]]:
    from littrans.fidelity import _native

    return list(_native(page)[0])


# --- layout evidence in the record --------------------------------------------------------


def test_layout_results_belong_to_the_record_and_runs_do_not(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(_pdf(root / "source" / "book.pdf"), root, "technical-book", "Fixture")
    store = root / "derived" / "fidelity-layout"
    store.mkdir(parents=True)
    for name in ("abc.json", "abc.request.json", "abc.log", "def.json"):
        (store / name).write_text("{}\n", encoding="utf-8")
    must_track, must_ignore, _ = record_sets(root)
    assert {"derived/fidelity-layout/abc.json", "derived/fidelity-layout/def.json"} <= must_track
    assert {"derived/fidelity-layout/abc.request.json", "derived/fidelity-layout/abc.log"} <= must_ignore
    ignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "derived/fidelity-layout/*.request.json" in ignore and "derived/fidelity-layout/" + "\n" not in ignore


def _record_detect(calls: list[list[Path]], fingerprint: str) -> Any:
    def detect(images: list[Path], store: Path) -> dict[str, Any]:
        calls.append(list(images))
        payload = {"status": "ok", "fingerprint": fingerprint,
                   "pages": {sha256_file(image): [] for image in images}, "images": {image.name: sha256_file(image) for image in images}}
        store.mkdir(parents=True, exist_ok=True)
        write_json(layout_detector.layout_result_path(store, fingerprint), payload)
        return payload
    return detect


def test_a_rerun_without_the_detector_cuts_pages_on_their_recorded_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_fidelity_source import approve

    import littrans.fidelity as fidelity

    root = tmp_path / "project"
    initialize_project(_pdf(root / "source" / "book.pdf", pages=2), root, "technical-book", "Fixture")
    calls: list[list[Path]] = []
    monkeypatch.setattr(fidelity, "detect_layout", _record_detect(calls, "runtime-a"))
    prepare_source(root)
    approve(root, "1,2")
    fingerprints = {p: read_json(root / f"derived/fidelity-pages/p{p:04d}.json")["fingerprint"] for p in (1, 2)}
    # No detector at all on this host: the recorded results carry the rerun.
    monkeypatch.setattr(fidelity, "detect_layout", lambda images, store: {"status": "unavailable", "reason": "no runtime here", "pages": {}})
    result = prepare_source(root, "2", replace=True)
    assert result["reused_layout_pages"] == [2] and result["detected_layout_pages"] == [] and result["layout_status"] == "reused"
    assert result["retained_receipt_pages"] == [2]
    assert read_json(root / "derived/fidelity-pages/p0002.json")["fingerprint"] == fingerprints[2]
    assert verify_fidelity(root)["passed"]
    # The retained receipt keeps the page's units verified; the rerun is not fresh work.
    from littrans.models import SourceUnit
    from littrans.storage import read_jsonl

    assert {u.verification_status.value for u in read_jsonl(root / "derived/units.jsonl", SourceUnit)} == {"verified"}
    # A page whose ledger records no usable result is detected, and without a runtime that
    # is refused unless the user allows it.
    (root / "derived/fidelity-layout" / "runtime-a.json").unlink()
    with pytest.raises(ValueError, match="Layout runtime unavailable"):
        prepare_source(root, "1", replace=True)
    monkeypatch.setattr(fidelity, "detect_layout", _record_detect(calls, "runtime-b"))
    result = prepare_source(root, "1", replace=True)
    assert result["detected_layout_pages"] == [1] and result["reused_layout_pages"] == [] and result["layout_status"] == "ok"
    assert [image.name for image in calls[-1]] == ["fidelity-p0001.png"]
    # `redetect` runs the detector although the recorded result is present.
    result = prepare_source(root, "1-2", replace=True, redetect=True)
    assert result["detected_layout_pages"] == [1, 2] and result["reused_layout_pages"] == []
    # A page prepared without any result (a forced re-detection while the runtime is
    # off) is detected on the next rerun.
    monkeypatch.setattr(fidelity, "detect_layout", lambda images, store: {"status": "unavailable", "reason": "off", "pages": {}})
    prepare_source(root, "1", replace=True, redetect=True, allow_missing_layout=True)
    assert read_json(root / "derived/fidelity-pages/p0001.json")["layout_status"] == "unavailable"
    monkeypatch.setattr(fidelity, "detect_layout", _record_detect(calls, "runtime-c"))
    result = prepare_source(root, "1", replace=True)
    assert result["detected_layout_pages"] == [1] and read_json(root / "derived/fidelity-pages/p0001.json")["layout_fingerprint"] == "runtime-c"


def test_a_clone_verifies_and_renders_the_same_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The git round trip: what one host commits, another host verifies and renders alike."""
    from test_fidelity_source import approve

    import littrans.fidelity as fidelity
    from littrans.source_render import render_source_review

    root = tmp_path / "origin" / "project"
    initialize_project(_pdf(root / "source" / "book.pdf"), root, "technical-book", "Fixture")
    monkeypatch.setattr(fidelity, "detect_layout", _record_detect([], "runtime-a"))
    prepare_source(root)
    approve(root, "1")
    checkpoint = Path(render_source_review(root, "1", "checkpoint")["html"]).read_bytes()
    live = record_sets(root)[2]
    ignore = root / ".gitignore"
    ignore.write_text(ignore.read_text(encoding="utf-8") + "".join(f"!packets/{name}/packet.json\n!packets/{name}/coverage.html\n" for name in live), encoding="utf-8")

    def git(*args: str, cwd: Path = root) -> None:
        subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@example.org", "-c", "user.name=t", *args], check=True, capture_output=True)

    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "record")
    clone = tmp_path / "clone-with-a-longer-directory-name"
    subprocess.run(["git", "clone", "-q", str(root), str(clone)], check=True, capture_output=True)
    (clone / "source").mkdir(exist_ok=True)
    (clone / "source" / "book.pdf").write_bytes((root / "source" / "book.pdf").read_bytes())
    assert (clone / "derived/fidelity-layout/runtime-a.json").is_file()
    assert not (clone / "derived/fidelity-layout/runtime-a.request.json").exists()
    monkeypatch.setattr(fidelity, "detect_layout", lambda images, store: {"status": "unavailable", "reason": "no runtime", "pages": {}})
    assert verify_fidelity(clone)["passed"]
    result = prepare_source(clone, "1", replace=True)
    assert result["reused_layout_pages"] == [1] and result["retained_receipt_pages"] == [1]
    assert Path(render_source_review(clone, "1", "checkpoint")["html"]).read_bytes() == checkpoint
