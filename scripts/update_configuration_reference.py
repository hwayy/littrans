"""Refresh generated CLI parameter tables and versioned configuration schemas."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from littrans.cli import app
from littrans.project import write_schemas
from typer.main import get_command

ROOT = Path(__file__).resolve().parents[1] / "plugins/literature-translation"


def parameter_table(command) -> str:
    lines = ["| Parameter | Type | Required | Default | Choices | Meaning |",
             "| --- | --- | --- | --- | --- | --- |"]
    meanings = {"project": "Project directory.", "local": "Select machine bindings; cannot override project policy.",
                "old": "Existing workspace; retained unchanged.", "new": "New workspace directory; must not exist.",
                "effective": "Resolve host and role inheritance.", "host": "Coordination host or auto.",
                "key": "Dot-separated configuration key.", "value": "String value unless --json is supplied.",
                "json_value": "Parse VALUE as JSON.", "dry_run": "Validate and show changes without writing.",
                "expect": "Require this semantic SHA256 before writing.", "file": "Complete candidate YAML file.",
                "preset": "Initialization preset name.", "resource": "Fixed context resource name.",
                "manifest": "YAML mapping resources to candidate files, plus a decision reason."}
    for param in command.params:
        spelling = "/".join(param.opts + param.secondary_opts) if param.opts[0].startswith("-") else param.name.upper()
        default = "required" if param.required else json.dumps(param.default)
        choices = json.dumps(list(getattr(param.type, "choices", []) or []))
        meaning = meanings.get(param.name, getattr(param, "help", None) or "Command parameter; see command description.")
        lines.append(f"| `{spelling}` | `{param.type.name}` | {'yes' if param.required else 'no'} | `{default}` | `{choices}` | {meaning} |")
    return "\n".join(lines)


def schema_type(node: dict[str, Any], definitions: dict[str, Any], links: dict[str, str]) -> str:
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        if name in links:
            return links[name]
        return definitions[name].get("type", "object")
    if "anyOf" in node or "oneOf" in node:
        return " or ".join(schema_type(part, definitions, links)
                           for part in node.get("anyOf", node.get("oneOf", [])))
    kind = node.get("type", "object")
    if kind == "array":
        if "prefixItems" in node:
            return "tuple [" + ", ".join(schema_type(part, definitions, links)
                                         for part in node["prefixItems"]) + "]"
        return "array of " + schema_type(node.get("items", {}), definitions, links)
    if kind == "object" and isinstance(node.get("additionalProperties"), dict):
        return "map of " + schema_type(node["additionalProperties"], definitions, links)
    return kind


def insert_command(text: str, name: str, entry: str) -> str:
    owner = "Project commands" if name.startswith("project ") else "Configuration management"
    heading = "## " + owner + "\n"
    if heading not in text:
        if owner == "Project commands":
            raise ValueError("CLI reference is missing its Project commands section")
        text = text.replace("## Compatibility aliases", heading + "\n## Compatibility aliases", 1)
    start = text.index(heading) + len(heading)
    boundary = re.search(r"^## ", text[start:], re.MULTILINE)
    end = start + boundary.start() if boundary else len(text)
    return text[:end] + entry + text[end:]


def refresh_reference(text: str) -> str:
    root = get_command(app)
    commands = [(f"config {name}", command) for name, command in root.commands["config"].commands.items()]
    commands += [(f"context {name}", root.commands["context"].commands[name]) for name in ("show", "validate", "apply")]
    commands += [("project init", root.commands["project"].commands["init"])]
    commands += [("project upgrade", root.commands["project"].commands["upgrade"])]
    for name, command in commands:
        pattern = r"(^### littrans " + re.escape(name) + r"\n)(.*?)(?=^#{1,3} |\Z)"
        found = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
        table = parameter_table(command) if command.params else "No command-specific parameters."
        if found:
            body = found[2]
            if command.params:
                body = re.sub(r"\| Parameter \|.*?(?=\n\n)", table, body, count=1, flags=re.DOTALL)
            text = text[:found.start()] + found[1] + body + text[found.end():]
        else:
            description = command.help or "Registered CLI command."
            entry = (f"### littrans {name}\n\n{description}\n\n"
                     f"{table}\n\nExample:\n\n```text\nlittrans {name} --help\n```\n\n")
            text = insert_command(text, name, entry)
    # Keep hand-written explanations and factory defaults; refresh schema metadata only.
    from littrans.models import SourceUnit, TranslationRecord
    from littrans.task_models import TaskEnvelope
    links = {name: f"[{name}](#model-{name.lower()})"
             for name in re.findall(r"^### Model (\w+)$", text, re.MULTILINE)}
    links.update({name: f"[{name}](#enum-{name.lower()})"
                  for name in re.findall(r"^### Enum (\w+)$", text, re.MULTILINE)})
    links["ParagraphData"] = "[paragraph properties](paragraph-model.md#paragraph-properties)"
    for model in (SourceUnit, TranslationRecord, TaskEnvelope):
        pattern = r"(^### Model " + model.__name__ + r"\n)(.*?)(?=^#{1,3} |\Z)"
        found = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
        if not found:
            continue
        body = found[2]
        old = {line.split("|")[1].strip().strip("`"): [cell.strip() for cell in line.split("|")[1:-1]]
               for line in body.splitlines() if line.startswith("| `")}
        schema = model.model_json_schema()
        rows = []
        for name, field in model.model_fields.items():
            node = schema["properties"][name]
            kind = schema_type(node, schema.get("$defs", {}), links)
            if name in old and field.default_factory is not None:
                default = old[name][3]
            else:
                default = "`required`" if field.is_required() else f"`{json.dumps(field.get_default(call_default_factory=True))}`"
            meaning = old[name][4] if name in old else field.description or "See the data contract."
            rows.append(f"| `{name}` | {kind} | {'yes' if field.is_required() else 'no'} | {default} | {meaning} |")
        body = re.sub(r"(?:^\| `.*\n)+", "\n".join(rows) + "\n", body, count=1, flags=re.MULTILINE)
        text = text[:found.start()] + found[1] + body + text[found.end():]
    return text


def main() -> None:
    path = ROOT / "references/cli-reference.md"
    path.write_text(refresh_reference(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    write_schemas(ROOT / "schemas")
    from littrans.configuration import yaml_text
    from littrans.settings import preset, settings_schema
    for name in ("technical-book", "research-paper"):
        (ROOT / "profiles" / f"{name}.yaml").write_text(
            "# Generated expanded example; runtime uses settings.yaml, never this file.\n" +
            yaml_text(preset(name, "Example document").payload()), encoding="utf-8", newline="\n")
    lines = ["# Settings field ownership", "", "Generated from the settings model. Run `config schema` for types and constraints.", "",
             "| Field | Domain | Consumer | Change effect |", "| --- | --- | --- | --- |"]
    lines.extend(f"| `{name}` | {field['x-domain']} | {field['x-consumer']} | {field['x-change-effect']} |" for name, field in settings_schema()["x-fields"].items())
    (ROOT / "references/settings-fields.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
