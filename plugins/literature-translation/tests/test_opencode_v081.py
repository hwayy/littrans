"""OpenCode's native config must preserve both role policy and read-only boundaries."""
from pathlib import Path

import pytest
import yaml
from config_fixtures import save_project
from test_workflow_v6 import project as project_fixture

from littrans.agent_config import configure_agents, opencode_model
from littrans.hosts import host_model_defaults
from littrans.models import RoleDispatch
from littrans.project import dispatch_report
from littrans.storage import load_project
from littrans.tasks import create_task

project = project_fixture


def agent(project: Path, name: str) -> tuple[dict, str]:
    text = (project / f".opencode/agents/littrans-{name}.md").read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---", 2)
    return yaml.safe_load(frontmatter), body


def test_opencode_defaults_match_codex() -> None:
    defaults = host_model_defaults()
    assert set(defaults["opencode"]) == set(defaults["codex"])
    for role, policy in defaults["codex"].items():
        assert defaults["opencode"][role] == {
            "model": "openai/" + policy["model"], "reasoning_effort": policy["reasoning_effort"],
        }


def test_generated_roles_apply_models_and_v2_permissions_together(project: Path) -> None:
    result = configure_agents(project, "opencode", write=True)
    expected = {"translator": "openai/gpt-6-luna#max", "asset-transcriber": "openai/gpt-6-luna#max",
                "translation-reviewer": "openai/gpt-6-sol#high", "asset-reviewer": "openai/gpt-6-sol#high",
                "source-reviewer": "openai/gpt-6-sol#high", "document-scout": None,
                "terminology-researcher": None}
    for role, model in expected.items():
        head, body = agent(project, role)
        assert head.get("model") == model == result["models"]["littrans-" + role]
        assert head["mode"] == "subagent" and "permission" not in head
        denies = {rule["action"] for rule in head["permissions"]
                  if rule["effect"] == "deny" and rule["resource"] == "*"}
        assert "subagent" in denies
        if role in {"translation-reviewer", "asset-reviewer", "document-scout", "terminology-researcher"}:
            assert {"edit", "shell"} <= denies
        else:
            assert "edit" not in denies and "shell" not in denies
        assert f"instructions/roles/{role}.md" in body
        relative = f"../../.littrans/host-agents/opencode/roles/{role}.md"
        assert relative in body
        assert (project / ".opencode/agents" / relative).resolve().is_file()
        assert str(project) not in body  # definitions remain portable between machines
    assert configure_agents(project, "opencode")["changed"] == []


def test_existing_empty_policy_stays_unset_and_policy_changes_regenerate(project: Path) -> None:
    config = load_project(project)
    config.agent_models["opencode"] = {}
    save_project(project, config)
    configure_agents(project, "opencode", write=True)
    assert all(value is None for value in configure_agents(project, "opencode")["models"].values())
    config.agent_models["opencode"]["audit"] = RoleDispatch(model="deepseek/deepseek-flash", reasoning_effort="max")
    save_project(project, config)
    assert set(configure_agents(project, "opencode")["changed"]) == {
        ".opencode/agents/littrans-translation-reviewer.md",
        ".opencode/agents/littrans-translation-reviewer-fidelity.md",
        ".opencode/agents/littrans-translation-reviewer-technical.md",
        ".opencode/agents/littrans-translation-reviewer-chinese-style.md",
        ".opencode/agents/littrans-external-recheck.md",
    }
    configure_agents(project, "opencode", write=True)
    assert agent(project, "translation-reviewer")[0]["model"] == "deepseek/deepseek-flash#max"
    assert "model" not in agent(project, "translator")[0]
    config.agent_models["opencode"] = {}
    save_project(project, config)
    configure_agents(project, "opencode", write=True)
    assert "model" not in agent(project, "translation-reviewer")[0]


@pytest.mark.parametrize("model,effort,expected", [
    (None, None, None), ("openai/gpt-6-luna", None, "openai/gpt-6-luna"),
    ("openai/gpt-6-luna#max", None, "openai/gpt-6-luna#max"),
    ("openai/gpt-6-luna#max", "max", "openai/gpt-6-luna#max"),
    ("provider/model:tag", "high", "provider/model:tag#high"),
])
def test_model_variant_conversion(model: str | None, effort: str | None, expected: str | None) -> None:
    assert opencode_model(model, effort) == expected


@pytest.mark.parametrize("model,effort", [
    (None, "max"), ("gpt-6-luna", "max"), ("openai/gpt-6-luna#high", "max"),
    ("openai/gpt-6-luna", "two words"), ("openai/gpt-6-luna#", None),
    ("openai/gpt-6-luna\npermissions: []", None),
])
def test_bad_policy_does_not_write_agents(project: Path, model: str | None, effort: str | None) -> None:
    config = load_project(project)
    config.agent_models["opencode"]["audit"] = RoleDispatch(model=model, reasoning_effort=effort)
    save_project(project, config)
    with pytest.raises(ValueError, match="OpenCode"):
        configure_agents(project, "opencode", write=True)
    assert not (project / ".opencode").exists()


def test_native_configuration_advisory_and_portable_handoff(project: Path) -> None:
    report = dispatch_report(project, "opencode")
    assert report["supports"]["project_agent_config"]
    assert not report["supports"]["model"]  # not a per-call argument
    assert any("project agents PROJECT --host opencode --check" in note for note in report["advisories"])
    task = create_task(project, "translate", batch_ids=["sample-one-b001"], host="opencode")
    handoff = Path(task["handoff"])
    text = handoff.read_text(encoding="utf-8")
    assert "../../../.." in text and "not the shell's current directory or Git root" in text
    assert (handoff.parent / "../../../..").resolve() == project.resolve()
    assert (handoff.parent / "instructions/roles/translator.md").is_file()


def test_native_policy_regeneration_preserves_user_edits(project: Path) -> None:
    configure_agents(project, "opencode", write=True)
    path = project / ".opencode/agents/littrans-translation-reviewer.md"
    original = path.read_text(encoding="utf-8") + "\nUser instructions\n"
    path.write_text(original, encoding="utf-8")
    config = load_project(project)
    config.agent_models["opencode"]["audit"] = RoleDispatch(model="openai/gpt-6-luna", reasoning_effort="max")
    save_project(project, config)
    with pytest.raises(ValueError, match="User-edited"):
        configure_agents(project, "opencode", write=True)
    assert path.read_text(encoding="utf-8") == original
