"""Refresh generated CLI parameter tables and versioned configuration schemas."""
from __future__ import annotations

import json
import re
from pathlib import Path

from littrans.cli import app
from littrans.project import write_schemas
from typer.main import get_command

ROOT = Path(__file__).resolve().parents[1] / "plugins/literature-translation"


def parameter_table(command) -> str:
    lines = ["| Parameter | Type | Required | Default | Choices | Meaning |",
             "| --- | --- | --- | --- | --- | --- |"]
    meanings = {"project": "Project directory.", "local": "Select machine bindings; cannot override project policy.",
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


def main() -> None:
    path = ROOT / "references/cli-reference.md"
    text = path.read_text(encoding="utf-8")
    root = get_command(app)
    additions = []
    commands = [(f"config {name}", command) for name, command in root.commands["config"].commands.items()]
    commands += [(f"context {name}", root.commands["context"].commands[name]) for name in ("show", "validate", "apply")]
    commands += [("project init", root.commands["project"].commands["init"])]
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
            additions.append(f"### littrans {name}\n\nSee [configuration](configuration.md) for semantics and effects.\n\n"
                             f"{table}\n\nExample:\n\n```text\nlittrans {name} --help\n```\n\n")
    if additions:
        text = text.replace("## Compatibility aliases", "## Configuration management\n\n" + "".join(additions) + "## Compatibility aliases")
    # New fields in existing contracts retain their actual Pydantic field order.
    from littrans.models import TranslationRecord
    from littrans.task_models import TaskEnvelope
    for model in (TranslationRecord, TaskEnvelope):
        pattern = r"(^### Model " + model.__name__ + r"\n)(.*?)(?=^#{1,3} |\Z)"
        found = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
        if not found:
            continue
        body = found[2]
        old = {line.split("|")[1].strip().strip("`"): line for line in body.splitlines() if line.startswith("| `")}
        rows = []
        for name, field in model.model_fields.items():
            if name in {"code_annotations", "policy_snapshot", "policy_domains"}:
                node = model.model_json_schema()["properties"][name]
                kind = node.get("type", "object")
                default = "required" if field.is_required() else json.dumps(field.get_default(call_default_factory=True))
                rows.append(f"| `{name}` | {kind} | {'yes' if field.is_required() else 'no'} | `{default}` | Saved policy or supplemental code annotations; see configuration reference. |")
            else:
                rows.append(old[name])
        body = re.sub(r"(?:^\| `.*\n)+", "\n".join(rows) + "\n", body, count=1, flags=re.MULTILINE)
        text = text[:found.start()] + found[1] + body + text[found.end():]
    path.write_text(text, encoding="utf-8")
    write_schemas(ROOT / "schemas")
    from littrans.configuration import yaml_text
    from littrans.settings import preset, settings_schema
    for name in ("technical-book", "research-paper"):
        (ROOT / "profiles" / f"{name}.yaml").write_text(
            "# Generated expanded example; runtime uses settings.yaml, never this file.\n" +
            yaml_text(preset(name, "Example document").payload()), encoding="utf-8")
    lines = ["# Settings field ownership", "", "Generated from the settings model. Run `config schema` for types and constraints.", "",
             "| Field | Domain | Consumer | Change effect |", "| --- | --- | --- | --- |"]
    lines.extend(f"| `{name}` | {field['x-domain']} | {field['x-consumer']} | {field['x-change-effect']} |" for name, field in settings_schema()["x-fields"].items())
    (ROOT / "references/settings-fields.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
