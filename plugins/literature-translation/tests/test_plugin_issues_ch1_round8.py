"""Regressions for PLUGIN-ISSUES.md LT-037 and LT-038 (chapter-1 ledger, round 8).

LT-037: a packet's `model` is the dispatch value handed to the host's task launcher (an
alias such as `sonnet` on Claude Code, a concrete id elsewhere); the model a host serves
under it is a separate observation. Submissions echo the dispatch value and may record the
served label; external review verifies host evidence against `model_identity` when the
configured `model` is an alias, and an unverified run keeps the served label.

LT-038: a refused precondition reaches the CLI as a clean error with exit code 1, never as a
traceback, and the stale-packet guards say how to recover.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fidelity_fixtures import make_asset_fixture
from test_efficiency_v4 import _make_project, _packet_dir, _submit
from typer.testing import CliRunner

from littrans import cli, external_review
from littrans.models import ExternalReviewerConfig, ProjectConfig, WorkflowPacketManifest
from littrans.quality import run_qa
from littrans.representation_models import AssetSubmission
from littrans.representations import build_asset_packet, submit_candidates
from littrans.storage import atomic_write_text, read_json, write_json, write_jsonl
from littrans.workflow import create_workflow_packet

SERVED = "deepseek-v4.1-flash[1M]"
runner = CliRunner()


def _submission(packet: dict, **overrides: object) -> dict:
    return {
        "packet_id": packet["packet_id"], "author_task_id": "writer",
        "model": packet["model"], "reasoning_effort": packet["reasoning_effort"],
        "image_evidence": packet["required_images"],
        "candidates": [{"asset_id": aid, "format": "latex", "content": "1+1=2"} for aid in packet["asset_ids"]],
        **overrides,
    }


def test_candidate_echoes_dispatch_model_and_records_served_label(tmp_path: Path) -> None:
    root, _, _ = make_asset_fixture(tmp_path, [("Original context.", "math", "x=1")])
    packet = build_asset_packet(root, ["fixture-asset-1"])
    assert packet["model"] == "gpt-6-luna"  # the codex dispatch value copied from agent_models
    write_json(root / "served.json", _submission(packet, model=SERVED))
    with pytest.raises(ValueError, match="echo the dispatched packet's model policy"):
        submit_candidates(root, root / "served.json")
    write_json(root / "echo.json", _submission(packet, served_model_label=SERVED))
    result = submit_candidates(root, root / "echo.json")
    record = read_json(root / "evidence" / "representations" / "candidates" / f"{result['candidate_sha256']['fixture-asset-1']}.json")
    assert (record["model"], record["served_model_label"]) == (packet["model"], SERVED)


def test_dispatch_model_contracts_are_described() -> None:
    submission = AssetSubmission.model_json_schema()["properties"]
    assert "dispatch model" in submission["model"]["description"]
    assert "unverified" in submission["served_model_label"]["description"]
    assert "Not the served model" in WorkflowPacketManifest.model_json_schema()["properties"]["model"]["description"]
    assert "dispatch value" in ProjectConfig.model_json_schema()["properties"]["agent_models"]["description"]
    reviewer = ExternalReviewerConfig.model_json_schema()
    assert "host alias" in reviewer["properties"]["model_identity"]["description"]
    with pytest.raises(ValueError, match="model_identity must not be empty"):
        ExternalReviewerConfig(id="r", driver="claude-code", command="claude", model="sonnet", model_identity=" ")


def _claude_run(monkeypatch: pytest.MonkeyPatch, served: str) -> None:
    raw = json.dumps({
        "structured_output": {"verdict": "accepted", "summary": "No substantive defects found.", "issues": []},
        "modelUsage": {served: {"inputTokens": 1}},
        "fast_mode_state": "off",
    })
    monkeypatch.setattr(external_review.shutil, "which", lambda command: command)
    monkeypatch.setattr(external_review.subprocess, "run",
                        lambda command, *a, **k: subprocess.CompletedProcess(command, 0, raw, ""))


def test_alias_routed_elsewhere_fails_verification_but_keeps_the_served_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = ExternalReviewerConfig(id="r", driver="claude-code", command="claude", model="sonnet", effort="high")
    packet = tmp_path / "packet.md"
    packet.write_text("Review packet.", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    _claude_run(monkeypatch, SERVED)
    with pytest.raises(external_review.ExternalInvocationError) as info:
        external_review._invoke(reviewer, packet, work, {})
    assert f"requested=sonnet, expected=sonnet, served={SERVED}" in str(info.value)
    assert "set model_identity to the served id" in str(info.value)
    assert (info.value.failure_type, info.value.actual_model_label) == ("model", SERVED)
    attempts = [json.loads(line) for line in (work / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(a["failure_type"], a["actual_model"]) for a in attempts] == [("model", SERVED)]


def test_model_identity_verifies_the_served_model_behind_an_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = ExternalReviewerConfig(id="r", driver="claude-code", command="claude", model="sonnet",
                                      model_identity="deepseek-v4.1-flash", effort="high")
    packet = tmp_path / "packet.md"
    packet.write_text("Review packet.", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    _claude_run(monkeypatch, SERVED)
    result = external_review._invoke(reviewer, packet, work, {})
    assert (result[2], result[4]) == ("sonnet", SERVED)  # requested stays the dispatch value
    _claude_run(monkeypatch, "claude-sonnet-5")
    with pytest.raises(external_review.ExternalInvocationError, match="expected=deepseek-v4.1-flash, served=claude-sonnet-5$"):
        external_review._invoke(reviewer, packet, work, {})


def test_cursor_host_import_matches_model_identity(tmp_path: Path) -> None:
    reviewer = ExternalReviewerConfig(id="r", driver="cursor-cli", command="agent", model="cursor-grok-4.6-high-fast",
                                      fallbacks=[{"model": "sonnet-high", "model_identity": "claude-sonnet-5-high"}])
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"review_binding": "bind", "verdict": "accepted",
                                  "summary": "No substantive defects found.", "issues": []}), encoding="utf-8")
    loaded = external_review._load_cursor_host_result(reviewer, result, {}, "bind", "Sonnet 5 High")
    assert (loaded[2], loaded[4]) == ("sonnet-high", "Sonnet 5 High")
    with pytest.raises(ValueError, match="expected=\\['cursor-grok-4.6-high-fast', 'claude-sonnet-5-high'\\]"):
        external_review._load_cursor_host_result(reviewer, result, {}, "bind", "Sonnet 4.6 High")


def test_cli_reports_refused_preconditions_without_a_traceback(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, pages=1)
    batch = manifests[0]
    _submit(root, batch.batch_id)
    assert run_qa(root, batch.batch_id).passed
    packet = create_workflow_packet(root, "audit", [batch.batch_id], "fidelity")
    issues_path = _packet_dir(root, packet) / "issues.jsonl"
    write_jsonl(issues_path, [])
    style_path = root / "context" / "style-guide.md"
    atomic_write_text(style_path, style_path.read_text(encoding="utf-8").rstrip() + "\n\n- Newly mandatory terminology review.\n")

    stale = runner.invoke(cli.app, ["review", "import-set", str(root), str(_packet_dir(root, packet) / "manifest.json"), str(issues_path)])
    assert stale.exit_code == 1
    assert isinstance(stale.exception, SystemExit)
    assert "Audit packet context is stale" in stale.output
    assert "rebuild the audit packet" in stale.output.lower()
    assert "Traceback" not in stale.output

    invalid = runner.invoke(cli.app, ["workflow", "next", str(root), "--start-at", "no-such-batch"])
    assert (invalid.exit_code, isinstance(invalid.exception, SystemExit)) == (1, True)
    assert "references unknown batch" in invalid.output
