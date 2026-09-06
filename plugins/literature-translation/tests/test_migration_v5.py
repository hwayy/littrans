from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from littrans.migration import migrate_project_schema
from littrans.models import ProjectConfig
from littrans.project import rebuild_project
from littrans.storage import load_project, sha256_file, write_yaml


def _v4_project(root: Path) -> None:
    root.mkdir()
    source = root / "source" / "original.pdf"
    source.parent.mkdir()
    document = fitz.open()
    document.new_page().insert_text((50, 50), "Historical source.")
    document.save(source)
    document.close()
    write_yaml(root / "project.yaml", ProjectConfig(
        schema_version=4, project_id="migration-v5", title="Migration fixture",
        source_path="source/original.pdf", source_sha256=sha256_file(source),
        source_pages=1, profile="technical-book",
    ).model_dump(mode="json", exclude_none=True))
    packet = root / "packets" / "audit-legacy"
    packet.mkdir(parents=True)
    (packet / "manifest.json").write_text("{}\n", encoding="utf-8")
    (root / ".gitignore").write_text("/tmp/\n", encoding="utf-8")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("dry_run", [True, False])
def test_historical_in_place_migration_is_rejected_read_only(tmp_path: Path, dry_run: bool) -> None:
    root = tmp_path / "project"
    _v4_project(root)
    before = _snapshot(root)
    with pytest.raises(ValueError, match="project rebuild OLD NEW"):
        migrate_project_schema(root, 5, dry_run=dry_run)
    assert _snapshot(root) == before
    assert not (root / ".littrans").exists()


def test_rebuild_preserves_historical_packets_and_refuses_existing_destination(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    _v4_project(old)
    before = _snapshot(old)
    rebuild_project(old, new)
    assert _snapshot(old) == before
    assert load_project(new).schema_version == 6
    assert not (new / "packets" / "audit-legacy").exists()
    assert not (new / "derived" / "units.jsonl").exists()
    rebuilt = _snapshot(new)
    with pytest.raises(ValueError, match="new, non-existing directory"):
        rebuild_project(old, new)
    assert _snapshot(old) == before
    assert _snapshot(new) == rebuilt
