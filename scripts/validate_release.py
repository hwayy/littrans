from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "literature-translation"
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def load_json(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def package_version() -> str:
    init_text = (PLUGIN_ROOT / "src" / "littrans" / "__init__.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^__version__ = "([^"]+)"$', init_text, re.MULTILINE)
    if match is None:
        raise ValueError("littrans.__version__ is missing")
    return match.group(1)


def main() -> None:
    marketplace_path = ROOT / ".agents" / "plugins" / "marketplace.json"
    manifest_path = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
    pyproject_path = PLUGIN_ROOT / "pyproject.toml"

    marketplace = load_json(marketplace_path)
    manifest = load_json(manifest_path)
    with pyproject_path.open("rb") as stream:
        pyproject = tomllib.load(stream)

    if marketplace.get("name") != "littrans":
        raise ValueError("Marketplace name must be 'littrans'")

    entries = marketplace.get("plugins")
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError("Marketplace must contain exactly one plugin entry")
    entry = entries[0]
    if not isinstance(entry, dict):
        raise ValueError("Marketplace plugin entry must be an object")

    plugin_name = manifest.get("name")
    if plugin_name != "literature-translation" or entry.get("name") != plugin_name:
        raise ValueError("Plugin names in the marketplace and manifest do not match")

    source = entry.get("source")
    if not isinstance(source, dict) or source.get("source") != "local":
        raise ValueError("Marketplace plugin source must be local")
    if source.get("path") != "./plugins/literature-translation":
        raise ValueError("Marketplace plugin path is not the canonical repository path")

    manifest_version = manifest.get("version")
    project = pyproject.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject.toml is missing [project]")
    versions = {manifest_version, project.get("version"), package_version()}
    if len(versions) != 1 or not isinstance(manifest_version, str):
        raise ValueError(f"Release versions are not aligned: {sorted(map(str, versions))}")
    if SEMVER.fullmatch(manifest_version) is None:
        raise ValueError(f"Plugin version is not valid semantic versioning: {manifest_version}")
    if "+codex." in manifest_version:
        raise ValueError("A release must not contain a local Codex cachebuster")

    skills_path = manifest.get("skills")
    if skills_path != "./skills/" or not (PLUGIN_ROOT / "skills").is_dir():
        raise ValueError("Plugin skills path is invalid")
    skill_files = sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))
    if not skill_files:
        raise ValueError("Plugin contains no skills")

    for schema_path in sorted((PLUGIN_ROOT / "schemas").glob("*.json")):
        load_json(schema_path)

    print(
        f"Validated {plugin_name} {manifest_version}: "
        f"marketplace=littrans, skills={len(skill_files)}"
    )


if __name__ == "__main__":
    main()
