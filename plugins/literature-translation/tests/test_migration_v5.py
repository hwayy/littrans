from __future__ import annotations

from pathlib import Path

from littrans.migration import migrate_project_schema
from littrans.models import ProjectConfig
from littrans.storage import load_project, write_yaml


def _v4_project(root: Path) -> None:
    root.mkdir()
    write_yaml(
        root / "project.yaml",
        ProjectConfig(
            schema_version=4,
            project_id="migration-v5",
            title="Migration fixture",
            source_path="source/original.pdf",
            source_sha256="0" * 64,
            source_pages=1,
            profile="technical-book",
        ).model_dump(mode="json", exclude_none=True),
    )
    packet = root / "packets" / "audit-legacy"
    packet.mkdir(parents=True)
    (packet / "manifest.json").write_text("{}\n", encoding="utf-8")
    (root / ".gitignore").write_text("/tmp/\n", encoding="utf-8")


def test_v4_to_v5_dry_run_is_read_only(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _v4_project(root)

    result = migrate_project_schema(root, 5, dry_run=True)

    assert result["changed"] is False
    assert result["gitignore_update_required"] is True
    assert result["legacy_packet_cleanup_candidate_count"] == 1
    assert result["legacy_packet_cleanup_root"] == "packets"
    assert load_project(root).schema_version == 4
    assert not (root / ".littrans").exists()


def test_v4_to_v5_preserves_packets_and_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _v4_project(root)

    result = migrate_project_schema(root, 5)

    assert result["legacy_packets_deleted"] == 0
    assert (root / "packets" / "audit-legacy" / "manifest.json").is_file()
    assert (root / ".littrans" / "work").is_dir()
    assert (root / ".gitignore").read_text(encoding="utf-8").splitlines().count(
        "/.littrans/"
    ) == 1
    assert load_project(root).schema_version == 5

    repeated = migrate_project_schema(root, 5)
    assert repeated["changed"] is False
    assert (root / ".gitignore").read_text(encoding="utf-8").splitlines().count(
        "/.littrans/"
    ) == 1
