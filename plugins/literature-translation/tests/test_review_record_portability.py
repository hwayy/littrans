"""Regression coverage for record completeness and legacy layout portability."""

import subprocess
from pathlib import Path, PurePosixPath

import pymupdf
import pytest

from littrans import layout_detector
from littrans.project import initialize_project
from littrans.record import record_sets, record_tracking


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _project(root: Path, source: Path) -> None:
    source.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        document.new_page()
        document.save(source)
    initialize_project(source, root, "technical-book")


def test_representation_evidence_must_be_tracked(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _project(root, tmp_path / "book.pdf")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    names = [
        "index.json", "candidates/a.json", "responses/a.json", "packets/a.json",
        "reviews/a.json", "renders/a/comparison.html", "renders/a/assets/original.svg",
    ]
    relative = [f"evidence/representations/{name}" for name in names]
    for name in relative:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    assert set(relative) <= record_sets(root)[0]
    assert record_tracking(root)["problems"] == [
        f"meant for the record but not committed: {name}" for name in sorted(relative)
    ] + [f"neither tracked nor ignored (silently outside the record): {name}" for name in sorted(relative)]
    ignore = root / ".gitignore"
    original = ignore.read_text(encoding="utf-8")
    ignore.write_text(original + "\nevidence/representations/\n", encoding="utf-8")
    assert record_tracking(root)["problems"] == [
        f"meant for the record but .gitignore excludes it: {name}" for name in sorted(relative)
    ]
    ignore.write_text(original, encoding="utf-8")
    _git(root, "add", ".")
    assert record_tracking(root)["problems"] == []


@pytest.mark.parametrize("name", ["book.pdf", "inputs/book.pdf", "inputs/book.data"])
def test_configured_source_inside_project_cannot_be_tracked(tmp_path: Path, name: str) -> None:
    root = tmp_path / "project"
    _project(root, root / name)
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "add", "-f", "--", name)
    assert name in record_sets(root)[1]
    assert record_tracking(root)["problems"] == [f"must never be tracked but is: {name}"]


def test_source_outside_project_in_same_repository_cannot_be_tracked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    root = repo / "project"
    _project(root, repo / "inputs/book.pdf")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    assert record_tracking(root)["problems"] == [
        "must never be tracked but is: inputs/book.pdf (relative to repository root)"
    ]
    _git(repo, "rm", "--cached", "--", "inputs/book.pdf")
    assert record_tracking(root)["problems"] == []


def test_source_outside_repository_is_not_classified(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _project(root, tmp_path / "book.pdf")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    assert record_tracking(root)["problems"] == []


@pytest.mark.parametrize("key", [
    r"C:\old\evidence\pages\fidelity-p0001.png",
    "/old/evidence/pages/fidelity-p0001.png",
    r"C:\old/evidence\pages/fidelity-p0001.png",
])
def test_legacy_layout_paths_resolve_on_posix(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    # Model POSIX path parsing even when the regression suite runs on Windows.
    monkeypatch.setattr(layout_detector, "Path", PurePosixPath)
    image = PurePosixPath("/new/evidence/pages/fidelity-p0001.png")
    monkeypatch.setattr(PurePosixPath, "resolve", lambda self: self, raising=False)
    boxes = [{"label": "table"}]
    assert layout_detector.layout_page_items({"pages": {key: boxes}}, image, None) == boxes


def test_legacy_layout_ambiguity_and_exact_key_precedence(tmp_path: Path) -> None:
    image = tmp_path / "fidelity-p0001.png"
    pages = {
        r"C:\old\fidelity-p0001.png": [{"label": "table"}],
        "/old/fidelity-p0001.png": [{"label": "figure"}],
    }
    assert layout_detector.layout_page_items({"pages": pages}, image, None) is None
    exact = [{"label": "title"}]
    pages[str(image.resolve())] = exact
    assert layout_detector.layout_page_items({"pages": pages}, image, None) == exact
    pages["digest"] = []
    assert layout_detector.layout_page_items({"pages": pages}, image, "digest") == []
