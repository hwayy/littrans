"""Release paths use production initialization, persistence and rebuilding."""
from __future__ import annotations

import shutil
from pathlib import Path

import pymupdf

from littrans.models import ProjectStatus
from littrans.project import initialize_project, rebuild_project
from littrans.storage import load_project, save_project, sha256_file


def test_initialized_project_moves_and_rebuild_reloads(tmp_path: Path) -> None:
    root = tmp_path / "original"
    source = root / "source" / "book.pdf"
    source.parent.mkdir(parents=True)
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Portable project source.")
        document.save(source)
    initialize_project(source, root, "technical-book")
    config = load_project(root)
    config.status = ProjectStatus.PREPARED
    save_project(root, config)
    moved = tmp_path / "moved"
    shutil.move(root, moved)
    loaded = load_project(moved)
    assert sha256_file(moved / loaded.source_path) == loaded.source_sha256
    assert loaded.status == ProjectStatus.PREPARED
    rebuilt = tmp_path / "rebuilt"
    rebuild_project(moved, rebuilt)
    restored = load_project(rebuilt)
    assert sha256_file(rebuilt / restored.source_path) == loaded.source_sha256
    assert restored.status == ProjectStatus.INITIALIZED
    assert not list((rebuilt / "translations").glob("*.jsonl"))
