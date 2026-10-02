"""Generated metadata must preserve the reference's domain structure and explanations."""
from __future__ import annotations

import re
import runpy
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
REFERENCE = PLUGIN / "references/cli-reference.md"
GENERATOR = PLUGIN.parent.parent / "scripts/update_configuration_reference.py"


def refresh(text: str) -> str:
    return str(runpy.run_path(str(GENERATOR))["refresh_reference"](text))


def owning_section(text: str, command: str) -> str:
    before = text.split(f"### littrans {command}\n", 1)[0]
    return re.findall(r"^## (.+)$", before, re.MULTILINE)[-1]


def test_regeneration_preserves_explanations_and_is_stable() -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    text = text.replace("OLD is unchanged;", "A manually maintained migration explanation. OLD is unchanged;")
    text = text.replace("Logical paragraph head; shared by its fragments.",
                        "A manually maintained relationship explanation.")
    original_headings = re.findall(r"^#{1,3} .+$", text, re.MULTILINE)
    generated = refresh(text)
    assert generated == refresh(generated)
    assert re.findall(r"^#{1,3} .+$", generated, re.MULTILINE) == original_headings
    assert "A manually maintained migration explanation." in generated
    assert "A manually maintained relationship explanation." in generated
    assert owning_section(generated, "project upgrade") == "Project commands"
    assert generated.count("## Configuration management\n") == 1


def test_missing_commands_are_added_to_their_existing_domain_section() -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    for command in ("project upgrade", "config show", "context show"):
        pattern = r"^### littrans " + re.escape(command) + r"\n.*?(?=^#{1,3} |\Z)"
        text, count = re.subn(pattern, "", text, count=1, flags=re.MULTILINE | re.DOTALL)
        assert count == 1
    generated = refresh(text)
    assert owning_section(generated, "project upgrade") == "Project commands"
    assert owning_section(generated, "config show") == "Configuration management"
    assert owning_section(generated, "context show") == "Configuration management"
    assert generated.count("## Configuration management\n") == 1
    assert generated == refresh(generated)


def test_reference_types_follow_nullable_and_mapping_schema_fields() -> None:
    generated = refresh(REFERENCE.read_text(encoding="utf-8"))
    for name in ("paragraph_id", "item_id", "parent_item_id", "container_role", "section_id"):
        assert f"| `{name}` | string or null |" in generated
    assert "| `label_translations` | map of string |" in generated
    assert "| `paragraph` | [paragraph properties]" in generated
