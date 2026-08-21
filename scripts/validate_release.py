from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from littrans.project import schema_mismatches


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
    cursor_marketplace_path = ROOT / ".cursor-plugin" / "marketplace.json"
    manifest_path = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
    cursor_manifest_path = PLUGIN_ROOT / ".cursor-plugin" / "plugin.json"
    pyproject_path = PLUGIN_ROOT / "pyproject.toml"

    marketplace = load_json(marketplace_path)
    cursor_marketplace = load_json(cursor_marketplace_path)
    manifest = load_json(manifest_path)
    cursor_manifest = load_json(cursor_manifest_path)
    with pyproject_path.open("rb") as stream:
        pyproject = tomllib.load(stream)

    if marketplace.get("name") != "littrans":
        raise ValueError("Marketplace name must be 'littrans'")
    if cursor_marketplace.get("name") != "littrans":
        raise ValueError("Cursor marketplace name must be 'littrans'")

    entries = marketplace.get("plugins")
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError("Marketplace must contain exactly one plugin entry")
    entry = entries[0]
    if not isinstance(entry, dict):
        raise ValueError("Marketplace plugin entry must be an object")

    cursor_entries = cursor_marketplace.get("plugins")
    if not isinstance(cursor_entries, list) or len(cursor_entries) != 1:
        raise ValueError("Cursor marketplace must contain exactly one plugin entry")
    cursor_entry = cursor_entries[0]
    if not isinstance(cursor_entry, dict):
        raise ValueError("Cursor marketplace plugin entry must be an object")

    plugin_name = manifest.get("name")
    if plugin_name != "literature-translation" or entry.get("name") != plugin_name:
        raise ValueError("Plugin names in the marketplace and manifest do not match")
    if cursor_manifest.get("name") != plugin_name or cursor_entry.get("name") != plugin_name:
        raise ValueError("Plugin names in the Cursor marketplace and manifest do not match")

    source = entry.get("source")
    if not isinstance(source, dict) or source.get("source") != "local":
        raise ValueError("Marketplace plugin source must be local")
    if source.get("path") != "./plugins/literature-translation":
        raise ValueError("Marketplace plugin path is not the canonical repository path")
    if cursor_entry.get("source") != "plugins/literature-translation":
        raise ValueError("Cursor marketplace plugin path is not the canonical repository path")

    manifest_version = manifest.get("version")
    cursor_version = cursor_manifest.get("version")
    project = pyproject.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject.toml is missing [project]")
    versions = {
        manifest_version,
        cursor_version,
        project.get("version"),
        package_version(),
    }
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
    agent_files = sorted((PLUGIN_ROOT / "agents").glob("*.md"))
    expected_agents = {
        "literature-translator.md": "writer",
        "literature-fidelity-reviewer.md": "jsonl",
        "literature-technical-reviewer.md": "jsonl",
        "literature-chinese-style-reviewer.md": "jsonl",
        "literature-external-reviewer.md": "bound-json",
    }
    actual_agents = {path.name for path in agent_files}
    if actual_agents != set(expected_agents):
        raise ValueError(
            "Cursor plugin agent set is invalid: "
            f"missing={sorted(set(expected_agents) - actual_agents)}, "
            f"unexpected={sorted(actual_agents - set(expected_agents))}"
        )
    frontmatter_re = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", re.DOTALL)
    write_file_re = re.compile(
        r"write jsonl|return the issue file path|including an empty file",
        re.IGNORECASE,
    )
    for agent_path in agent_files:
        agent_text = agent_path.read_text(encoding="utf-8")
        parsed = frontmatter_re.match(agent_text)
        if parsed is None:
            raise ValueError(f"Agent is missing YAML frontmatter: {agent_path.name}")
        frontmatter, body = parsed.group(1), parsed.group(2)
        readonly = bool(
            re.search(r"^readonly:\s*true\s*$", frontmatter, re.MULTILINE)
        )
        contract = expected_agents[agent_path.name]
        if contract == "writer" and readonly:
            raise ValueError("Cursor translation writer must not be read-only")
        if contract != "writer" and not readonly:
            raise ValueError(f"Reviewer agent must be read-only: {agent_path.name}")
        if readonly:
            if write_file_re.search(body):
                raise ValueError(
                    f"Read-only agent {agent_path.name} must return content instead "
                    "of writing files"
                )
            lowered_body = body.lower()
            if "return" not in lowered_body:
                raise ValueError(
                    f"Read-only agent {agent_path.name} must return its result"
                )
            if contract == "jsonl" and "jsonl" not in lowered_body:
                raise ValueError(
                    f"Read-only agent {agent_path.name} must return JSONL "
                    "content for the parent to persist"
                )
            if contract == "bound-json" and not all(
                marker in lowered_body
                for marker in ("json object", "review_binding", "verdict", "issues")
            ):
                raise ValueError(
                    "External reviewer must return a bound JSON object with "
                    "review_binding, verdict, and issues"
                )

    schema_dir = PLUGIN_ROOT / "schemas"
    mismatches = schema_mismatches(schema_dir)
    if mismatches:
        raise ValueError(f"Tracked schemas do not match runtime models: {mismatches}")

    print(
        f"Validated {plugin_name} {manifest_version}: "
        f"marketplace=littrans, skills={len(skill_files)}, agents={len(agent_files)}"
    )


if __name__ == "__main__":
    main()
