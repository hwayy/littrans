"""Check the actual reference against registered interfaces, not a second command list."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest
from test_workflow_v6 import project as project_fixture
from typer.main import get_command
from typer.testing import CliRunner

from littrans import (
    cli,
    fidelity_models,
    models,
    representation_models,
    structure_profile,
    task_models,
)

PLUGIN = Path(__file__).resolve().parents[1]
REFERENCE = PLUGIN / "references/cli-reference.md"
project = project_fixture


def sections(text: str, prefix: str) -> dict[str, str]:
    matches = list(re.finditer(r"^### " + re.escape(prefix) + r"(.+)$", text, re.M))
    names = [match[1] for match in matches]
    assert len(names) == len(set(names)), "Duplicate reference headings"
    result = {}
    for match in matches:
        tail = text[match.end():]
        boundary = re.search(r"^#{1,3} ", tail, re.M)
        result[match[1]] = tail[:boundary.start()] if boundary else tail
    return result


def rows(section: str) -> list[list[str]]:
    return [
        [html.unescape(cell.strip().strip("`")) for cell in line.strip("|").split("|")]
        for line in section.splitlines() if line.startswith("| `")
    ]


def commands(command, prefix: str = "") -> dict:
    found = {}
    for name, child in command.commands.items():
        if child.hidden:
            continue
        path = (prefix + " " + name).strip()
        if hasattr(child, "commands"):
            found.update(commands(child, path))
        else:
            found[path] = child
    return found


def assert_parameters(section: str, params: list) -> None:
    documented = rows(section)
    assert len(documented) == len(params), "Missing, duplicate or obsolete parameters"
    for row, param in zip(documented, params, strict=True):
        spelling = (
            "/".join(param.opts + param.secondary_opts)
            if param.opts[0].startswith("-") else param.name.upper()
        )
        assert row[0] == spelling
        assert row[1] == param.type.name
        assert row[2] == ("yes" if param.required else "no")
        if param.required:
            assert row[3] == "required"
        else:
            assert json.loads(row[3]) == param.default
        assert json.loads(row[4]) == list(getattr(param.type, "choices", []) or [])
        assert row[5].strip(), f"Missing explanation: {spelling}"


def test_reference_covers_registered_commands_and_parameters() -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    documented = sections(text, "littrans ")
    root = get_command(cli.app)
    current = commands(root)
    assert documented.keys() == current.keys(), "CLI reference command drift"
    for path, command in current.items():
        assert_parameters(documented[path], command.params)
        assert f"littrans {path}" in documented[path]
        assert "Example:" in documented[path]
        result = CliRunner().invoke(cli.app, [*path.split(), "--help"])
        assert result.exit_code == 0, result.output
    root_section = text.split("### Root options\n", 1)[1].split("## Runtime commands", 1)[0]
    assert_parameters(root_section, root.params)


def test_reference_check_detects_parameter_drift() -> None:
    section = sections(REFERENCE.read_text(encoding="utf-8"), "littrans ")["task claim"]
    params = get_command(cli.app).commands["task"].commands["claim"].params
    for changed in (
        section.replace('`"fresh-session"`', '`"subagent"`'),
        section.replace("| `--executor` | `str` | yes", "| `--executor` | `str` | no"),
        section.replace("| `--mode`", "| `--removed-option`"),
    ):
        with pytest.raises(AssertionError):
            assert_parameters(changed, params)


MODULES = (models, fidelity_models, representation_models, task_models, structure_profile)


def record_model(name: str):
    return next(getattr(module, name) for module in MODULES if hasattr(module, name))


def test_documented_record_fields_and_examples() -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    for name, section in sections(text, "Model ").items():
        fields = record_model(name).model_fields
        field_rows = rows(section)
        assert [row[0] for row in field_rows] == list(fields), name
        for row in field_rows:
            assert row[2] == ("yes" if fields[row[0]].is_required() else "no"), (name, row[0])
    examples = re.findall(r"<!-- example-model: (\w+) -->\s*```json\n(.*?)\n```", text, re.S)
    assert examples
    for name, payload in examples:
        record_model(name).model_validate(json.loads(payload))
    # Every JSON example must parse, including templates that require real packet bindings.
    for payload in re.findall(r"```json\n(.*?)\n```", text, re.S):
        json.loads(payload)
    from littrans.external_review import _validate_result

    for payload in re.findall(r"<!-- example-validator: external-result -->\s*```json\n(.*?)\n```", text, re.S):
        _validate_result(json.loads(payload))


def test_translation_example_with_real_synthetic_bindings(project: Path) -> None:
    from littrans.batching import load_manifest
    from littrans.context_packets import original_context
    from littrans.storage import read_jsonl, write_jsonl
    from littrans.translation import submit_translation

    text = REFERENCE.read_text(encoding="utf-8")
    example = re.search(r"<!-- example-model: TranslationRecord -->\s*```json\n(.*?)\n```", text, re.S)
    assert example
    payload = json.loads(example[1])
    batch_id = "sample-one-b001"
    editable = set(load_manifest(project, batch_id).translatable_unit_ids)
    units = [unit for unit in read_jsonl(project / "derived/units.jsonl", models.SourceUnit)
             if unit.unit_id in editable]
    records = [models.TranslationRecord.model_validate({
        **payload,
        "unit_id": unit.unit_id,
        "source_hash": unit.source_hash,
        "image_evidence": original_context(project, [unit])["required_images"],
    }) for unit in units]
    incoming = project / "documented-example.jsonl"
    write_jsonl(incoming, records)
    stored = submit_translation(project, batch_id, incoming)
    assert {record.unit_id for record in stored} == editable
    # A valid submission is not a claim of translation quality or audit approval.
    assert all(record.status == models.ProjectStatus.DRAFT for record in stored)
    records[0].source_hash = "stale-source-binding"
    write_jsonl(incoming, records)
    with pytest.raises(ValueError, match="Source hash mismatch"):
        submit_translation(project, batch_id, incoming)


def assert_links(document: Path, text: str) -> None:
    for link in re.findall(r"\]\(([^)\s]+)\)", text):
        if "://" in link or link.startswith("mailto:"):
            continue
        filename, _, fragment = link.partition("#")
        target = document.parent / filename if filename else document
        assert target.is_file(), (document, link)
        if fragment and target.suffix == ".md":
            headings = re.findall(r"^#+ (.+)$", target.read_text(encoding="utf-8"), re.M)
            anchors = {re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-") for heading in headings}
            assert fragment in anchors, (document, link)


def test_document_navigation_and_readme_budget() -> None:
    repository = PLUGIN.parent.parent
    for directory in (PLUGIN / "references", PLUGIN / "roles", PLUGIN / "skills"):
        for document in directory.rglob("*.md"):
            assert_links(document, document.read_text(encoding="utf-8"))
    for document, maximum in ((repository / "README.md", 400), (PLUGIN / "README.md", 500)):
        text = document.read_text(encoding="utf-8")
        assert len(text.split()) <= maximum
        assert not re.search(r"\d+\.\d+", text.splitlines()[0])
        assert_links(document, text)
    document = repository / "CONTRIBUTING.md"
    assert_links(document, document.read_text(encoding="utf-8"))
