"""Regressions reported by the Codex review of PR #11."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
from typer.testing import CliRunner

import littrans
from littrans.build_info import build_digest
from littrans.cli import app
from littrans.fidelity import _auto_formula_conditions
from littrans.glossary import glossary_check
from littrans.models import SourceUnit, UnitKind
from littrans.project import initialize_project
from littrans.record import record_tracking
from littrans.rendering import _continues_paragraph
from littrans.storage import load_project, save_project


def _pdf(path: Path) -> Path:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Review fixture.")
    document.save(path)
    document.close()
    return path


def test_project_init_rolls_back_scaffold_failure_and_can_retry(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "book.pdf")
    repo = tmp_path / "repo"
    root = repo / "workspace"
    root.mkdir(parents=True)
    (root / "keep.txt").write_text("user data", encoding="utf-8")
    (root / ".gitignore").write_text("*.bak\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "LITTRANS.md").write_text("previous notes", encoding="utf-8")
    (repo / "README.md").write_text("user readme", encoding="utf-8")
    (repo / "tools").write_text("reserved", encoding="utf-8")

    runner = CliRunner()
    args = ["project", "init", str(source), str(root), "--repo-root", str(repo)]
    failed = runner.invoke(app, args)
    assert failed.exit_code != 0
    assert not (root / "project.yaml").exists()
    assert not (root / "derived" / "provenance.json").exists()
    assert not (root / "context" / "document-brief.md").exists()
    assert not (repo / "AGENTS.md").exists()
    assert (root / "keep.txt").read_text(encoding="utf-8") == "user data"
    assert (root / ".gitignore").read_text(encoding="utf-8") == "*.bak\n"
    assert (repo / "README.md").read_text(encoding="utf-8") == "user readme"
    assert (repo / "docs" / "LITTRANS.md").read_text(encoding="utf-8") == "previous notes"
    assert (repo / "tools").read_text(encoding="utf-8") == "reserved"

    (repo / "tools").unlink()
    retried = runner.invoke(app, args)
    assert retried.exit_code == 0, retried.output
    payload = json.loads(retried.output)
    assert "tools/lt.py" in payload["scaffold"]["created"]
    assert (root / "project.yaml").is_file()
    assert load_project(root).record_root_relative == ".."


def test_cli_preserves_message_only_missing_file_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pdf"
    result = CliRunner().invoke(app, ["project", "init", str(missing), str(tmp_path / "project")],
                                terminal_width=200)
    assert result.exit_code == 1
    assert str(missing)[:20] in result.output  # Rich truncates long absolute paths in the CLI panel.
    assert "File not found: None" not in result.output


def test_project_launcher_uses_installed_wheel_without_plugin_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    initialize_project(_pdf(tmp_path / "book.pdf"), root, "technical-book")
    spec = importlib.util.spec_from_file_location("lt_wheel_fallback", root / "tools" / "lt.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("LITTRANS_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(module, "resolve_plugin_root", lambda: (_ for _ in ()).throw(SystemExit("missing")))
    calls: list[list[str]] = []

    def run(command: list[str], *, check: bool) -> SimpleNamespace:
        assert not check
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main(["doctor"]) == 0
    assert calls == [[sys.executable, "-m", "littrans", "doctor"]]
    monkeypatch.setenv("LITTRANS_PLUGIN_ROOT", str(tmp_path / "invalid"))
    with pytest.raises(SystemExit, match="missing"):
        module.main(["doctor"])


def _paragraph(text: str, *, page: int, **flags: bool) -> SourceUnit:
    return SourceUnit(
        unit_id=f"p{page}", kind=UnitKind.PARAGRAPH, page=page,
        bbox=(0, 0, 1, 1), source_text=text, source_hash="fixture", confidence=1,
        **flags,
    )


@pytest.mark.parametrize("ending", [
    "The result follows.[^1]",
    "*The result follows.[^1]*",
    "The result follows.*[^1]*",
    "The result follows.[^1][^2]",
])
def test_footnote_calls_do_not_hide_terminal_punctuation(ending: str) -> None:
    receiver = _paragraph("A new thought.", page=2, continues_from_previous=True)
    assert not _continues_paragraph(_paragraph(ending, page=1), receiver)
    assert _continues_paragraph(_paragraph(ending, page=1, continued_to_next=True), receiver)
    assert _continues_paragraph(_paragraph("The result follows[^1]", page=1), receiver)


def test_build_digest_includes_runtime_resources_but_excludes_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "plugin" / "src" / "littrans"
    template = package / "templates" / "bilingual.html.j2"
    vendor = package / "vendor" / "mathjax" / "tex-svg.js"
    profile = tmp_path / "plugin" / "profiles" / "host-models.yaml"
    for path in (package / "__init__.py", template, vendor, profile):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("initial", encoding="utf-8")
    monkeypatch.setattr(littrans, "__file__", str(package / "__init__.py"))
    try:
        build_digest.cache_clear()
        previous = build_digest()
        for path in (template, profile, vendor):
            path.write_text("changed", encoding="utf-8")
            build_digest.cache_clear()
            current = build_digest()
            assert current != previous, path
            previous = current
        cache = package / "__pycache__" / "module.pyc"
        cache.parent.mkdir()
        cache.write_bytes(b"compiled")
        (package / "templates" / "scratch.tmp").write_text("temporary", encoding="utf-8")
        build_digest.cache_clear()
        assert build_digest() == previous
    finally:
        build_digest.cache_clear()


def test_nested_record_root_requires_repository_files(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "book.pdf")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    record_root = repo / "records"
    root = record_root / "books" / "one"
    initialize_project(source, root, "technical-book", repo_root=record_root)
    assert load_project(root).record_root_relative == "../.."
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    assert record_tracking(root)["problems"] == []
    refreshed = CliRunner().invoke(app, ["project", "scaffold", str(root), "--refresh"])
    assert refreshed.exit_code == 0, refreshed.output
    assert json.loads(refreshed.output)["repo_root"] == str(record_root)
    assert not (root / "docs" / "LITTRANS.md").exists()
    assert record_tracking(root)["problems"] == []

    private_doc = record_root / "docs" / "private.pdf"
    private_doc.write_bytes(b"private source")
    subprocess.run(["git", "-C", str(repo), "add", "records/docs/private.pdf"], check=True)
    assert "must never be tracked but is: records/docs/private.pdf" in record_tracking(root)["problems"]
    private_doc.unlink()
    subprocess.run(["git", "-C", str(repo), "rm", "-q", "--cached", "records/docs/private.pdf"], check=True)
    extra_record = record_root / "docs" / "EXTRA.md"
    extra_record.write_text("new record", encoding="utf-8")
    assert "neither tracked nor ignored (silently outside the record): records/docs/EXTRA.md" in record_tracking(root)["problems"]
    extra_record.unlink()

    history = record_root / "docs" / "HISTORY.md"
    saved = history.read_bytes()
    history.unlink()
    assert "required record file is missing: records/docs/HISTORY.md" in record_tracking(root)["problems"]
    history.write_bytes(saved)
    subprocess.run(["git", "-C", str(repo), "rm", "-q", "--cached", "records/PLUGIN-ISSUES.md"], check=True)
    assert "meant for the record but not committed: records/PLUGIN-ISSUES.md" in record_tracking(root)["problems"]

    # A dev.4 project has no field. Its distinctive scaffold locates the ancestor.
    config = load_project(root)
    config.record_root_relative = None
    save_project(root, config)
    legacy_refresh = CliRunner().invoke(app, ["project", "scaffold", str(root), "--refresh"])
    assert legacy_refresh.exit_code == 0, legacy_refresh.output
    assert json.loads(legacy_refresh.output)["repo_root"] == str(record_root)
    assert not (root / "docs" / "LITTRANS.md").exists()
    assert "meant for the record but not committed: records/PLUGIN-ISSUES.md" in record_tracking(root)["problems"]

    private = root / "source" / "extra" / "licensed.pdf"
    private.parent.mkdir(parents=True)
    private.write_bytes(b"private source")
    subprocess.run([
        "git", "-C", str(repo), "add", "-f", "records/books/one/source/extra/licensed.pdf",
    ], check=True)
    output = root / "output" / "final.md"
    output.write_text("rendered edition", encoding="utf-8")
    subprocess.run([
        "git", "-C", str(repo), "add", "-f", "records/books/one/output/final.md",
    ], check=True)
    lock_trace = root / ".littrans-write-lock" / "trace"
    lock_trace.parent.mkdir()
    lock_trace.write_text("runtime", encoding="utf-8")
    subprocess.run([
        "git", "-C", str(repo), "add", "-f", "records/books/one/.littrans-write-lock/trace",
    ], check=True)
    problems = record_tracking(root)["problems"]
    assert "must never be tracked but is: records/books/one/source/extra/licensed.pdf" in problems
    assert "must never be tracked but is: records/books/one/output/final.md" in problems
    assert "tracked despite .gitignore exclusion: records/books/one/.littrans-write-lock/trace" in problems


@pytest.mark.parametrize("word", ["Otherwise", "True", "Undefined"])
def test_bare_case_words_remain_formula_conditions(word: str) -> None:
    glyphs = [
        {"id": str(i), "text": char, "font": "CMR10", "size": 10,
         "bbox": [10 + i * 6, 10, 16 + i * 6, 20], "line": "b1-l0"}
        for i, char in enumerate(word)
    ]
    region = {"glyph_ids": [glyph["id"] for glyph in glyphs]}
    by_id = {glyph["id"]: glyph for glyph in glyphs}
    assert [item["source_text"] for item in _auto_formula_conditions(
        region, by_id, measured_ink=False,
    )] == [word]


def test_applied_capitalized_operator_is_still_not_a_condition() -> None:
    text = "Mean(x)"
    glyphs = [
        {"id": str(i), "text": char, "font": "CMMI10" if char == "x" else "CMR10",
         "size": 10, "bbox": [10 + i * 6, 10, 16 + i * 6, 20], "line": "b1-l0"}
        for i, char in enumerate(text)
    ]
    assert _auto_formula_conditions(
        {"glyph_ids": [glyph["id"] for glyph in glyphs]},
        {glyph["id"]: glyph for glyph in glyphs}, measured_ink=False,
    ) == []


@pytest.mark.parametrize(("content", "error"), [
    ("terms:\n  - Hölder\n", r"terms\[1\] must be a mapping"),
    ("terms:\n  - source: Hölder\n    target: 赫尔德\n    status: aproved\n", "unknown status"),
    ("terms:\n  - source: Hölder\n    status: [approved]\n", "unknown status"),
])
def test_glossary_check_refuses_silently_dropped_approved_terms(
    tmp_path: Path, content: str, error: str,
) -> None:
    root = tmp_path / "project"
    initialize_project(_pdf(tmp_path / "book.pdf"), root, "technical-book")
    (root / "glossary" / "approved.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        glossary_check(root)
    result = CliRunner().invoke(app, ["glossary", "check", str(root)])
    assert result.exit_code == 1 and "Traceback" not in result.output
