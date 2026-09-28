"""Portable handoffs preserve the existing domain gates and durable evidence."""
import tomllib
from pathlib import Path

import pytest
from test_workflow_v6 import project as project_fixture

from littrans.agent_config import configure_agents
from littrans.batching import load_manifest
from littrans.context_management import context_impact, save_context_snapshot
from littrans.hosts import detect_coordination_host, resolve_coordination_host
from littrans.models import SourceUnit, TranslationRecord
from littrans.quality import audit_coverage
from littrans.storage import read_json, read_jsonl, write_json, write_jsonl
from littrans.tasks import claim_task, create_task, receive_task, task_status

project = project_fixture


def task_directory(project: Path, result: dict) -> Path:
    return Path(result["handoff"]).parent


def test_unknown_host_is_portable_and_opencode_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODEX_CI", raising=False)
    assert detect_coordination_host() == "generic"
    assert resolve_coordination_host("opencode") == "opencode"


def test_translation_handoff_receive_and_replay_preserve_gates(project: Path) -> None:
    task = create_task(project, "translate", batch_ids=["sample-one-b001"], host="generic")
    directory = task_directory(project, task)
    claim_task(project, task["task_id"], "fresh-writer", "fresh-session")
    envelope = read_json(directory / "task.json")
    packet = envelope["packet"]
    original = read_json(project / packet["files"]["original-images"])
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    editable = load_manifest(project, "sample-one-b001").translatable_unit_ids
    records = [TranslationRecord(unit_id=unit.unit_id, source_hash=unit.source_hash,
                                 target_text=unit.source_text.replace("The identity holds.", "该恒等式成立。"),
                                 image_evidence=original["required_images"])
               for unit in units if unit.unit_id in editable]
    write_jsonl(directory / "result.json", records)
    assert task_status(project, task["task_id"])["result_available"]
    result = receive_task(project, task["task_id"])
    assert result["state"] == "imported"
    # Import records QA, it cannot manufacture any audit approval.
    assert result["outcome"]["passed"]
    assert all(audit_coverage(project, "sample-one-b001")["missing"].values())
    assert receive_task(project, task["task_id"])["replayed"]
    for lens in ("fidelity", "technical", "chinese-style"):
        audit = create_task(project, "audit", batch_ids=["sample-one-b001"], lens=lens)
        with pytest.raises(ValueError, match="different executor"):
            claim_task(project, audit["task_id"], "fresh-writer", "fresh-session")
        claim_task(project, audit["task_id"], "synthetic-oracle-" + lens, "fresh-session")
        (task_directory(project, audit) / "result.json").write_text("", encoding="utf-8")
        assert receive_task(project, audit["task_id"])["state"] == "imported"
    assert not any(audit_coverage(project, "sample-one-b001")["missing"].values())
    (directory / "result.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be replaced"):
        receive_task(project, task["task_id"])


def test_changed_context_refuses_before_translation_write(project: Path) -> None:
    task = create_task(project, "translate", batch_ids=["sample-one-b001"])
    directory = task_directory(project, task)
    claim_task(project, task["task_id"], "writer", "subagent")
    (directory / "result.json").write_text("", encoding="utf-8")
    guide = project / "context/style-guide.md"
    guide.write_text(guide.read_text(encoding="utf-8") + "\nChanged rule.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="context changed"):
        receive_task(project, task["task_id"])
    assert not list((project / "translations").glob("*.jsonl"))


def test_claim_conflict_and_tampered_envelope(project: Path) -> None:
    first = create_task(project, "translate", batch_ids=["sample-one-b001"], host="codex")
    second = create_task(project, "translate", batch_ids=["sample-one-b001"], host="generic")
    claim_task(project, first["task_id"], "a", "subagent")
    with pytest.raises(ValueError, match="Conflicting writer"):
        claim_task(project, second["task_id"], "b", "fresh-session")
    path = task_directory(project, first) / "task.json"
    payload = read_json(path)
    payload["batch_ids"] = ["sample-two-b001"]
    write_json(path, payload)
    with pytest.raises(ValueError, match="envelope changed"):
        task_status(project, first["task_id"])


def test_proposal_receive_does_not_edit_glossary(project: Path) -> None:
    task = create_task(project, "terminology", pages="1", objective="Identify terms", host="generic")
    directory = task_directory(project, task)
    before = (project / "glossary/approved.yaml").read_bytes()
    claim_task(project, task["task_id"], "researcher", "fresh-session")
    write_json(directory / "result.json", {"proposals": [], "unresolved": []})
    received = receive_task(project, task["task_id"])
    assert received["outcome"] == {"proposal_received": True, "approved": False}
    assert (project / "glossary/approved.yaml").read_bytes() == before


def test_source_result_must_match_task_packet(project: Path) -> None:
    task = create_task(project, "source-review", pages="1")
    directory = task_directory(project, task)
    claim_task(project, task["task_id"], "source-reviewer", "subagent")
    write_json(directory / "result.json", {"packet_id": "wrong"})
    with pytest.raises(ValueError, match="another task"):
        receive_task(project, task["task_id"], confirm_visual_review=True)


@pytest.mark.parametrize("host", ["codex", "opencode"])
def test_project_agents_are_optional_and_preserve_user_edits(project: Path, host: str) -> None:
    report = configure_agents(project, host)
    assert report["changed"] and not report["written"]
    assert not (project / f".{host}/agents").exists()
    configure_agents(project, host, write=True)
    from littrans.record import agent_record_files
    assert f".{host}/agents/littrans-translator.{'toml' if host == 'codex' else 'md'}" in agent_record_files(project)
    if host == "opencode":
        skills = list((project / ".opencode/skills").glob("*/SKILL.md"))
        assert len(skills) == 4
        assert all("../../../.littrans/host-agents/opencode/references/" in
                   skill.read_text(encoding="utf-8") for skill in skills)
    assert configure_agents(project, host)["changed"] == []
    suffix = "toml" if host == "codex" else "md"
    agent = project / f".{host}/agents/littrans-translator.{suffix}"
    text = agent.read_text(encoding="utf-8")
    if host == "opencode":
        assert 'model: "openai/gpt-6-luna#max"' in text
    else:
        assert "model =" not in text and "model:" not in text
    agent.write_text(text + "\n# User edit\n", encoding="utf-8")
    with pytest.raises(ValueError, match="User-edited"):
        configure_agents(project, host, write=True)
    assert agent.read_text(encoding="utf-8").endswith("# User edit\n")


def test_context_impact_preserves_snapshot(project: Path) -> None:
    path = project / "context/before.json"
    save_context_snapshot(project, path)
    original = path.read_bytes()
    guide = project / "context/style-guide.md"
    guide.write_text("New rules.\n", encoding="utf-8")
    impact = context_impact(project, path)
    assert impact["changed"] == ["context/style-guide.md"]
    assert "invalidate" in impact["effects"][0]
    with pytest.raises(ValueError, match="already exists"):
        save_context_snapshot(project, path)
    assert path.read_bytes() == original


def test_relative_project_always_returns_absolute_handoff(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(project.parent)
    relative = Path(project.name)
    task = create_task(relative, "translate", batch_ids=["sample-one-b001"], host="codex")
    claimed = claim_task(relative, task["task_id"], "writer", "subagent")
    listed = task_status(relative)["tasks"][0]
    assert task["handoff"] == claimed["handoff"] == listed["handoff"]
    assert Path(claimed["handoff"]).is_absolute()
    assert Path(claimed["handoff"]).is_file()


@pytest.mark.parametrize("containing_workspace", [False, True])
def test_codex_agents_anchor_snapshots_and_disable_delegation(project: Path, containing_workspace: bool) -> None:
    root = project.parent if containing_workspace else project
    configure_agents(project, "codex", workspace=root, write=True)
    from littrans.agent_config import READ_ONLY

    agents = list((root / ".codex/agents").glob("*.toml"))
    assert len(agents) == 7
    for path in agents:
        definition = tomllib.loads(path.read_text(encoding="utf-8"))
        role = path.stem.removeprefix("littrans-")
        instructions = definition["developer_instructions"]
        assert f"instructions/roles/{role}.md" in instructions
        assert "saved instructions take precedence" in instructions
        assert "project root identified by the handoff" in instructions
        fallback = f"../../.littrans/host-agents/codex/roles/{role}.md"
        assert fallback in instructions
        assert (path.parent / fallback).is_file()
        assert str(root) not in instructions
        assert definition["agents"]["enabled"] is False
        assert (definition.get("sandbox_mode") == "read-only") == (role in READ_ONLY)
        assert "model" not in definition and "model_reasoning_effort" not in definition
    assert configure_agents(project, "codex", workspace=root)["changed"] == []
