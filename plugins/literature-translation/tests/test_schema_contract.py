from __future__ import annotations

from pathlib import Path

from littrans.project import schema_mismatches, write_schemas


def test_schema_validation_detects_stale_generated_files(tmp_path: Path) -> None:
    write_schemas(tmp_path)
    assert schema_mismatches(tmp_path) == []

    (tmp_path / "source-unit.schema.json").write_text("{}\n", encoding="utf-8")
    assert schema_mismatches(tmp_path) == ["stale:source-unit.schema.json"]
