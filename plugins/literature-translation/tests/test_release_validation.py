from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _release_validator() -> ModuleType:
    script = Path(__file__).resolve().parents[3] / "scripts" / "validate_release.py"
    spec = importlib.util.spec_from_file_location("littrans_release_validator_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_validation_rejects_generated_workspaces_but_allows_package_metadata(
    tmp_path: Path,
) -> None:
    module = _release_validator()
    plugin_root = tmp_path / "literature-translation"
    (plugin_root / "project" / ".littrans").mkdir(parents=True)
    generated = plugin_root / "nested" / "mathvision-temp"
    (generated / "evidence").mkdir(parents=True)
    (generated / "project.yaml").write_text("schema_version: 5\n", encoding="utf-8")
    module.PLUGIN_ROOT = plugin_root

    assert module.unexpected_plugin_workspaces() == [
        "nested/mathvision-temp",
        "nested/mathvision-temp/evidence",
    ]
