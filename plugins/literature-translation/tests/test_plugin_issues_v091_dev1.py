"""0.9.1-dev.1: LT-091 (again), LT-098..LT-100 from the migration of a project to 0.9.

PyMuPDF's own message channel stays off stdout (LT-091); a rebuild reports the copied
context files LitTrans does not validate (LT-098) and stages beside NEW so the project
inherits NEW's permissions (LT-099); task instruction snapshots survive Git's line-ending
conversion (LT-100). Synthetic projects only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from test_workflow_v6 import project as workflow_project

import littrans
from littrans import tasks
from littrans.project import rebuild_project
from littrans.resources import resource_root
from littrans.storage import read_json, sha256_file, sha256_text, staging_directory
from littrans.tasks import create_task

project = workflow_project

SRC = Path(littrans.__file__).resolve().parents[1]

# ---------------------------------------------------------------------------------------
# LT-091: opening an SVG echoes MuPDF warnings through PyMuPDF's message channel.
# ---------------------------------------------------------------------------------------

DANGLING_USE = (b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                b'width="10" height="10"><use xlink:href="#nope"/></svg>')


def test_pymupdf_messages_stay_off_stdout() -> None:
    """A real process: PyMuPDF binds its stream at import, which pytest's capture would hide."""
    script = (
        "import pymupdf\n"
        "from littrans import cli\n"
        f"with pymupdf.open(stream={DANGLING_USE!r}, filetype='svg') as document:\n"
        "    document[0].get_pixmap()\n"
        "cli.emit({'ok': True})\n"
        "cli.relay_mupdf_warnings()\n"
    )
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            encoding="utf-8", env=env, check=True)
    assert json.loads(result.stdout) == {"ok": True}
    assert "svg: cannot find linked symbol" in result.stderr
    assert result.stderr.count("LitTrans MuPDF warning: svg: cannot find linked symbol") == 1


# ---------------------------------------------------------------------------------------
# LT-098: copied files LitTrans does not validate are reported.
# ---------------------------------------------------------------------------------------


def test_rebuild_reports_copied_files_it_does_not_validate(project: Path) -> None:
    (project / "context/extraction-manifest.json").write_text('{"generation": 3}\n', encoding="utf-8")
    (project / "context/chapters").mkdir(exist_ok=True)
    (project / "context/chapters/.gitkeep").write_text("", encoding="utf-8")
    (project / "context/decisions.jsonl").write_text('{"reason": "kept"}\n', encoding="utf-8")
    (project / "glossary/notes.yaml").write_text("notes: []\n", encoding="utf-8")
    report: dict[str, object] = {}
    rebuilt = project.parent / "rebuilt"
    rebuild_project(project, rebuilt, report=report)
    expected = ["context/extraction-manifest.json", "glossary/notes.yaml"]
    assert report["unvalidated"] == expected
    provenance = read_json(rebuilt / "derived/rebuild-provenance.json")
    assert provenance["unvalidated"] == expected
    assert any(all(name in action for name in expected)
               for action in provenance["configuration"]["next_actions"])
    # Copied as they were; only reported.
    assert (rebuilt / "context/extraction-manifest.json").read_text(encoding="utf-8") == '{"generation": 3}\n'


def test_rebuild_of_known_context_reports_nothing(project: Path) -> None:
    report: dict[str, object] = {}
    rebuild_project(project, project.parent / "rebuilt", report=report)
    assert report["unvalidated"] == []


# ---------------------------------------------------------------------------------------
# LT-099: staging directories inherit the destination's permissions.
# ---------------------------------------------------------------------------------------


def test_rebuild_stages_without_tempfile(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("tempfile directories carry a restrictive ACL on Windows")

    monkeypatch.setattr(tempfile, "mkdtemp", refuse)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", refuse)
    rebuilt = project.parent / "rebuilt"
    rebuild_project(project, rebuilt)
    assert (rebuilt / "project.yaml").is_file()
    assert not list(project.parent.glob(".littrans-rebuild-*"))


def test_staging_directory_is_removed_after_failure(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), staging_directory(tmp_path, ".stage-") as staged:
        (staged / "file.txt").write_text("x", encoding="utf-8")
        raise RuntimeError
    assert not list(tmp_path.glob(".stage-*"))


def _acl(path: Path) -> list[str]:
    output = subprocess.run(["icacls", str(path)], capture_output=True, text=True, check=True).stdout
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    # First line starts with the path; the last reports the processed count.
    return [lines[0][len(str(path)):].strip(), *lines[1:-1]]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL inheritance")
def test_rebuilt_project_inherits_destination_permissions(project: Path) -> None:
    plain = project.parent / "plain"
    plain.mkdir()
    rebuilt = project.parent / "rebuilt"
    rebuild_project(project, rebuilt)
    assert _acl(rebuilt) == _acl(plain)
    with staging_directory(project.parent, ".stage-") as staged:
        assert _acl(staged) == _acl(plain)


# ---------------------------------------------------------------------------------------
# LT-100: instruction snapshots are LF text, verified whatever Git does to line endings.
# ---------------------------------------------------------------------------------------


@pytest.fixture
def crlf_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An installed plugin whose Markdown carries CRLF, as a Windows checkout may."""
    installed = tmp_path / "installed"
    for folder in ("roles", "references"):
        for path in (resource_root() / folder).glob("*.md"):
            target = installed / folder / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            text = path.read_bytes().replace(b"\r\n", b"\n")
            target.write_bytes(text.replace(b"\n", b"\r\n"))
    monkeypatch.setattr(tasks, "resource_root", lambda: installed)
    return installed


def _task(project: Path) -> tuple[Path, dict[str, object]]:
    status = create_task(project, "translate", batch_ids=["sample-one-b001"])
    directory = Path(status["handoff"]).parent
    return directory, read_json(directory / "task.json")


def test_instruction_snapshot_is_lf_text(project: Path, crlf_resources: Path) -> None:
    directory, task = _task(project)
    instructions = task["instructions"]
    assert isinstance(instructions, dict) and "roles/translator.md" in instructions
    for name, digest in instructions.items():
        snapshot = (directory / "instructions" / name).read_bytes()
        assert b"\r" not in snapshot
        assert digest == sha256_text(snapshot.decode("utf-8"))
        installed = (crlf_resources / name).read_bytes().replace(b"\r\n", b"\n")
        assert snapshot == installed


def test_instruction_check_survives_line_ending_conversion(project: Path, crlf_resources: Path) -> None:
    directory, task = _task(project)
    snapshot = directory / "instructions/roles/translator.md"
    snapshot.write_bytes(snapshot.read_bytes().replace(b"\n", b"\r\n"))  # core.autocrlf checkout
    tasks._check_instructions(directory, task)
    snapshot.write_bytes(snapshot.read_bytes().replace(b"Translator", b"Rewriter", 1) + b"extra\r\n")
    with pytest.raises(ValueError, match="Task instructions changed: roles/translator.md"):
        tasks._check_instructions(directory, task)


def test_instruction_check_accepts_raw_digest_of_earlier_builds(project: Path, crlf_resources: Path) -> None:
    directory, task = _task(project)
    snapshot = directory / "instructions/roles/translator.md"
    snapshot.write_bytes(snapshot.read_bytes().replace(b"\n", b"\r\n"))
    legacy = {**task, "instructions": {"roles/translator.md": sha256_file(snapshot)}}
    tasks._check_instructions(directory, legacy)
