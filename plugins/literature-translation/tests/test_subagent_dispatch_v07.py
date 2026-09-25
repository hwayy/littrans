"""Source review as a dispatch role, and Claude effort fixed by the agents (0.7)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from test_workflow_v6 import project as workflow_project
from typer.testing import CliRunner

from littrans.cli import app
from littrans.hosts import DISPATCH_ROLES, SUBAGENT_DISPATCH, dispatch_advisories
from littrans.models import RoleDispatch
from littrans.project import dispatch_report
from littrans.storage import load_project, read_json, save_project
from littrans.workflow import create_workflow_packet, workflow_next

project = workflow_project
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_source_review_is_a_dispatch_role_with_shipped_defaults(project: Path) -> None:
    assert "source-review" in DISPATCH_ROLES
    config = load_project(project)
    assert config.dispatch("codex", "source-review") == RoleDispatch(model="gpt-6-sol", reasoning_effort="high")
    assert config.dispatch("codex", "audit") == RoleDispatch(model="gpt-6-sol", reasoning_effort="high")
    assert config.dispatch("claude", "source-review") == RoleDispatch(model="sonnet")


def test_review_packets_report_the_dispatch_beside_a_host_independent_packet(project: Path) -> None:
    runner = CliRunner()
    codex = runner.invoke(app, ["source", "review-packets", str(project), "--pages", "1", "--host", "codex"])
    claude = runner.invoke(app, ["source", "review-packets", str(project), "--pages", "1", "--host", "claude"])
    assert codex.exit_code == 0 and claude.exit_code == 0, codex.output + claude.output
    first, second = json.loads(codex.stdout), json.loads(claude.stdout)
    assert first["packet_id"] == second["packet_id"]
    assert first["dispatch"] == {"host": "codex", "role": "source-review",
                                 "model": "gpt-6-sol", "reasoning_effort": "high"}
    assert second["dispatch"] == {"host": "claude", "role": "source-review",
                                  "model": "sonnet", "reasoning_effort": None}
    assert "dispatch" not in read_json(Path(first["packet_path"]))


def test_a_source_review_stage_carries_its_role_dispatch(project: Path) -> None:
    bid = "sample-one-b001"
    (project / "evidence/pages/fidelity-p0001.review.json").unlink()
    wave = workflow_next(project, start_at=bid, through=bid, host="codex")
    assert wave["stage"] == "source-review"
    task = next(task for task in wave["ready_tasks"] if task["stage"] == "source-review")
    assert (task["model"], task["reasoning_effort"]) == ("gpt-6-sol", "high")
    assert "literature-source-reviewer" in task["instruction"]
    packet = create_workflow_packet(project, "source-review", [bid], host="codex")
    assert isinstance(packet, dict)
    assert packet["dispatch"]["role"] == "source-review"
    assert packet["dispatch"]["model"] == "gpt-6-sol"


def test_a_configured_claude_effort_is_reported_as_not_applied(project: Path) -> None:
    assert SUBAGENT_DISPATCH["claude"].agent_effort == "high"
    assert dispatch_report(project, "claude")["advisories"] == []
    config = load_project(project)
    config.agent_models["claude"]["translate"] = RoleDispatch(model="sonnet", reasoning_effort="max")
    save_project(project, config)
    notes = dispatch_report(project, "claude")["advisories"]
    assert len(notes) == 1
    assert "translate.reasoning_effort is set to max" in notes[0] and "not applied" in notes[0]
    assert "`effort: high`" in notes[0]
    # Codex takes the effort per dispatch, so the same value there is simply applied.
    assert dispatch_advisories("codex", "translate", "gpt-6-luna", "max") == ()


def test_every_dispatch_agent_declares_the_claude_agent_effort() -> None:
    effort = SUBAGENT_DISPATCH["claude"].agent_effort
    agents = {path.stem: path.read_text(encoding="utf-8") for path in (PLUGIN_ROOT / "agents").glob("*.md")}
    assert "literature-source-reviewer" in agents
    for name, text in agents.items():
        declared = re.search(r"^effort:\s*(\S+)\s*$", text.split("\n---", 1)[0], re.MULTILINE)
        if name == "literature-external-reviewer":
            continue  # a Cursor external-review gate, not a dispatch role
        assert declared and declared.group(1) == effort, name


def test_the_plugin_handbook_lists_every_role_and_the_agent_effort(project: Path) -> None:
    handbook = next(project.rglob("LITTRANS.md")).read_text(encoding="utf-8")
    for role in DISPATCH_ROLES:
        assert f"`{role}`" in handbook
    assert "claude model yes/effort no (agents run at `effort: high`)" in handbook
