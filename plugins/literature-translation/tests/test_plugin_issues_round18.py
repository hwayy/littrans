"""Per-role dispatch policy, and a model policy that advises instead of blocking (round 18)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from test_asset_representation import project as asset_project
from test_workflow_v6 import project as workflow_project

from littrans.fidelity_models import load_assets
from littrans.hosts import COORDINATION_HOSTS, dispatch_advisories, host_model_defaults
from littrans.models import ProjectConfig, RoleDispatch
from littrans.project import dispatch_report
from littrans.representations import build_asset_packet, submit_candidates
from littrans.storage import load_project, save_project, write_json
from littrans.workflow import create_workflow_packet, workflow_next

project = workflow_project
assets = asset_project


def _config(**agent_models: dict[str, RoleDispatch]) -> ProjectConfig:
    return ProjectConfig(
        project_id="p", title="t", source_path="s.pdf", source_sha256="0" * 64,
        source_pages=1, profile="technical-book", agent_models=agent_models,
    )


# --- each role carries its own model and effort -----------------------------------------


def test_translate_and_transcribe_carry_their_own_model_and_effort(project: Path) -> None:
    config = load_project(project)
    config.agent_models["codex"] = {
        "translate": RoleDispatch(model="writer-model", reasoning_effort="low"),
        "transcribe": RoleDispatch(model="scribe-model", reasoning_effort="max"),
    }
    save_project(project, config)
    translate = create_workflow_packet(project, "translate", ["sample-one-b001"], host="codex")
    assert (translate.model, translate.reasoning_effort) == ("writer-model", "low")
    transcribe = build_asset_packet(project, [next(iter(load_assets(project)))], host="codex")
    assert (transcribe["model"], transcribe["reasoning_effort"]) == ("scribe-model", "max")


def test_audit_and_asset_audit_take_their_own_policy(project: Path) -> None:
    config = load_project(project)
    config.agent_models["codex"] = {
        "translate": RoleDispatch(model="writer-model", reasoning_effort="low"),
        "asset-audit": RoleDispatch(model="reviewer-model", reasoning_effort="high"),
    }
    save_project(project, config)
    # The reviewer role no longer inherits the writer's effort.
    assert load_project(project).dispatch("codex", "asset-audit") == RoleDispatch(
        model="reviewer-model", reasoning_effort="high"
    )
    assert load_project(project).dispatch("codex", "audit") == RoleDispatch()


# --- the legacy flat form still reads ---------------------------------------------------


def test_legacy_flat_policy_normalizes_and_rewrites_nested(project: Path) -> None:
    payload = yaml.safe_load((project / "project.yaml").read_text(encoding="utf-8"))
    payload["agent_models"] = {
        "codex": {"translate": "legacy-model", "transcribe": "legacy-model",
                  "reasoning_effort": "max"},
        "claude": {"translate": "sonnet"},
    }
    (project / "project.yaml").write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_project(project)
    # One shared effort becomes the default of every role that states none.
    assert config.dispatch("codex", "translate") == RoleDispatch(model="legacy-model",
                                                                 reasoning_effort="max")
    assert config.dispatch("codex", "transcribe") == RoleDispatch(model="legacy-model",
                                                                  reasoning_effort="max")
    assert config.dispatch("claude", "translate") == RoleDispatch(model="sonnet")
    save_project(project, config)
    rewritten = yaml.safe_load((project / "project.yaml").read_text(encoding="utf-8"))
    assert rewritten["agent_models"]["codex"]["translate"] == {"model": "legacy-model",
                                                               "reasoning_effort": "max"}
    assert rewritten["agent_models"]["claude"]["translate"] == {"model": "sonnet"}


def test_a_misspelled_role_or_host_is_named_rather_than_silently_unconfigured() -> None:
    with pytest.raises(ValueError, match="unsupported role: translte"):
        _config(claude={"translte": RoleDispatch(model="sonnet")})
    with pytest.raises(ValueError, match="unsupported host: antigravity"):
        ProjectConfig(project_id="p", title="t", source_path="s.pdf", source_sha256="0" * 64,
                      source_pages=1, profile="technical-book",
                      agent_models={"antigravity": {}})


# --- an unset model is a supported choice, never a blocked workflow ----------------------


@pytest.mark.parametrize("host", COORDINATION_HOSTS)
def test_an_unconfigured_role_dispatches_on_the_host_default(project: Path, host: str) -> None:
    config = load_project(project)
    config.agent_models = {name: {} for name in COORDINATION_HOSTS}
    save_project(project, config)
    packet = create_workflow_packet(project, "translate", ["sample-one-b001"], host=host)
    assert (packet.host, packet.model, packet.reasoning_effort) == (host, None, None)
    transcribe = build_asset_packet(project, [next(iter(load_assets(project)))], host=host)
    assert (transcribe["model"], transcribe["reasoning_effort"]) == (None, None)


def test_host_model_defaults_leave_cursor_and_qoder_to_their_own_policy() -> None:
    defaults = host_model_defaults()
    assert defaults["cursor"] == {} and defaults["qoder"] == {}


# --- advisories, not refusals -----------------------------------------------------------


def test_dispatch_advisories_report_both_directions_of_the_capability_gap() -> None:
    # Codex takes both, so a fully configured role has nothing to report.
    assert dispatch_advisories("codex", "translate", "gpt-5.6-luna", "max") == ()
    unset = dispatch_advisories("codex", "translate", None, None)
    assert len(unset) == 2 and all("is not set" in note for note in unset)
    # Claude Code takes a model but no per-dispatch effort.
    claude = dispatch_advisories("claude", "translate", "sonnet", "high")
    assert len(claude) == 1 and "frontmatter" in claude[0]
    assert dispatch_advisories("claude", "translate", None, None)[0].endswith(
        "default subagent model."
    )
    # Cursor and Qoder take neither, so only a configured value is worth reporting.
    assert dispatch_advisories("cursor", "translate", None, None) == ()
    assert dispatch_advisories("qoder", "transcribe", None, None) == ()
    cursor = dispatch_advisories("cursor", "translate", "cursor-model", None)
    assert len(cursor) == 1 and "still records the value" in cursor[0]


def test_a_value_the_host_cannot_take_is_recorded_rather_than_dropped(project: Path) -> None:
    config = load_project(project)
    config.agent_models["qoder"] = {
        "translate": RoleDispatch(model="qoder-model", reasoning_effort="high")
    }
    save_project(project, config)
    packet = create_workflow_packet(project, "translate", ["sample-one-b001"], host="qoder")
    assert (packet.model, packet.reasoning_effort) == ("qoder-model", "high")
    report = dispatch_report(project, "qoder")
    assert report["supports"] == {"model": False, "reasoning_effort": False}
    assert any("qoder-model" in note for note in report["advisories"])


def test_workflow_next_reports_the_advisories_of_the_roles_it_dispatches(project: Path) -> None:
    config = load_project(project)
    config.agent_models = {name: {} for name in COORDINATION_HOSTS}
    save_project(project, config)
    wave = workflow_next(project, host="claude")
    assert wave["stage"] == "translate"
    assert any("translate.model is not set" in note for note in wave["dispatch_advisories"])
    # Claude Code cannot take a per-dispatch effort, so an unset one is not worth a note.
    assert not any("reasoning_effort is not set" in note for note in wave["dispatch_advisories"])


# --- the submission echo follows the packet ---------------------------------------------


def test_a_modelless_packet_has_nothing_to_echo(assets: Path) -> None:
    config = load_project(assets)
    config.agent_models = {name: {} for name in COORDINATION_HOSTS}
    save_project(assets, config)
    packet = build_asset_packet(assets, ["a1"])
    assert packet["model"] is None
    payload = {"packet_id": packet["packet_id"], "author_task_id": "task-1",
               "image_evidence": packet["required_images"],
               "candidates": [{"asset_id": "a1", "format": "latex", "content": "x=1"}]}
    write_json(assets / "candidate.json", payload)
    assert submit_candidates(assets, assets / "candidate.json")["candidate_count"] == 1


def test_a_model_bearing_packet_still_requires_its_echo(assets: Path) -> None:
    config = load_project(assets)
    config.agent_models["codex"] = {
        "transcribe": RoleDispatch(model="scribe-model", reasoning_effort="max")
    }
    save_project(assets, config)
    packet = build_asset_packet(assets, ["a1"])
    base = {"packet_id": packet["packet_id"], "author_task_id": "task-1",
            "image_evidence": packet["required_images"],
            "candidates": [{"asset_id": "a1", "format": "latex", "content": "x=1"}]}
    write_json(assets / "candidate.json", base)
    with pytest.raises(ValueError, match="Record the packet's dispatch model"):
        submit_candidates(assets, assets / "candidate.json")
    write_json(assets / "candidate.json", {**base, "model": "other", "reasoning_effort": "max"})
    with pytest.raises(ValueError, match="echo the dispatched packet's model policy"):
        submit_candidates(assets, assets / "candidate.json")
    write_json(assets / "candidate.json",
               {**base, "model": "scribe-model", "reasoning_effort": "max"})
    assert submit_candidates(assets, assets / "candidate.json")["candidate_count"] == 1


# --- the CLI surfaces -------------------------------------------------------------------


def test_project_models_reports_the_resolved_policy_and_its_advisories(project: Path) -> None:
    from typer.testing import CliRunner

    from littrans.cli import app

    result = CliRunner().invoke(app, ["project", "models", str(project), "--host", "claude"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["host"] == "claude"
    assert report["supports"] == {"model": True, "reasoning_effort": False}
    assert report["roles"]["translate"] == {"model": "sonnet", "reasoning_effort": "high"}
    assert report["roles"]["audit"] == {"model": None, "reasoning_effort": None}
    assert any("audit.model is not set" in note for note in report["advisories"])


def test_advisories_stay_on_stderr_so_the_json_contract_is_unchanged(project: Path) -> None:
    from typer.testing import CliRunner

    from littrans.cli import app

    result = CliRunner().invoke(app, ["workflow", "next", str(project), "--host", "claude"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["host"] == "claude"
    assert "LitTrans advisory:" in result.stderr
    assert "LitTrans advisory:" not in result.stdout
