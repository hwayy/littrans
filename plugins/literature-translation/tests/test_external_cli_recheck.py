from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from littrans import external_review as external
from littrans.external_cli import (
    build_codex_command,
    build_opencode_command,
    codex_metadata,
    opencode_metadata,
)
from littrans.external_recheck import adjudicate_recheck, migrate_external_config
from littrans.models import (
    ExternalReviewConfig,
    ExternalReviewerConfig,
    ExternalReviewRun,
    PromptDelivery,
    ReviewUsage,
)
from littrans.storage import load_project, read_json, read_jsonl, save_project, write_json
from littrans.tasks import claim_task, create_task, receive_task
from littrans.workflow import workflow_next
from test_efficiency_v4 import _audit_and_approve, _make_project, _submit


def reviewer(driver: str = "codex-cli", identifier: str = "primary") -> ExternalReviewerConfig:
    return ExternalReviewerConfig(
        id=identifier,
        driver=driver,
        command={"codex-cli": "codex", "opencode-cli": "opencode", "claude-code": "claude"}.get(
            driver, "missing-cli"
        ),
        model={
            "codex-cli": "gpt-6-luna",
            "opencode-cli": "openai/gpt-6-luna",
            "claude-code": "sonnet",
        }.get(driver, "test"),
        effort="low",
    )


def project(tmp_path: Path) -> tuple[Path, str]:
    root, manifests = _make_project(tmp_path, 1)
    batch = manifests[0].batch_id
    config = load_project(root)
    config.external_review = ExternalReviewConfig(reviewer=reviewer())
    save_project(root, config)
    _submit(root, batch)
    _audit_and_approve(root, batch)
    return root, batch


def response(payload: dict, item: ExternalReviewerConfig) -> tuple:
    return (
        payload,
        json.dumps(payload),
        item.model,
        item.effort,
        item.model,
        None,
        1,
        PromptDelivery.FILE,
        0.1,
        ReviewUsage(),
        None,
    )


def finding(root: Path, batch: str) -> dict:
    unit, (source, target) = next(iter(external._evidence_map(root, batch).items()))
    return {
        "unit_id": unit,
        "severity": "major",
        "type": "meaning",
        "source_span": source[:20],
        "target_span": target,
        "explanation": "ORIGINAL_OPINION_SENTINEL",
        "suggested_revision": "",
        "confidence": 0.7,
    }


def test_models_and_effort_are_mapped_without_silent_loss(tmp_path: Path) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text("Evidence")
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages/page-0001.png").write_bytes(b"image")
    command = build_codex_command(reviewer(), packet, tmp_path)
    assert 'model_reasoning_effort="low"' in command and "--sandbox" in command
    assert "--image" in command
    command = build_opencode_command(reviewer("opencode-cli"), packet, "Review")
    assert "openai/gpt-6-luna#low" in command and "--standalone" in command
    assert "--variant" not in command and "--thinking" not in command
    assert "--file" in command
    with pytest.raises(ValueError, match="conflicts"):
        ExternalReviewerConfig(
            id="x", driver="opencode-cli", command="opencode", model="p/m#high", effort="low"
        )
    custom = ExternalReviewerConfig(
        id="x", driver="opencode-cli", command="opencode", model="p/m#custom"
    )
    assert "p/m#custom" in build_opencode_command(custom, packet, "Review")
    with pytest.raises(ValueError, match="effort must be omitted"):
        ExternalReviewerConfig(
            id="x", driver="cursor-cli", command="cursor", model="auto", effort="high"
        )
    with pytest.raises(ValueError, match="extra"):
        ExternalReviewerConfig(id="x", driver="codex-cli", command="codex", model="x", fallbacks=[])


def test_explicit_migration_preserves_history_and_flattens_fallbacks(tmp_path: Path) -> None:
    old = {
        "external_review": {
            "reviewers": [
                {
                    "id": "a",
                    "driver": "claude-code",
                    "command": "claude",
                    "model": "sonnet",
                    "effort": "high",
                    "fallbacks": [{"model": "opus", "effort": "low"}],
                },
                {"id": "b", "driver": "opencode-cli", "command": "opencode", "model": "p/m#high"},
            ],
            "assignment": "least-used",
            "assignment_since": "2026-01-01T00:00:00Z",
            "second_opinion": {"mode": "on-uncertainty", "confidence_below": 0.8},
        }
    }
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(old), encoding="utf-8")
    original = path.read_bytes()
    preview = migrate_external_config(tmp_path)
    assert path.read_bytes() == original
    assert [item["model"] for item in preview["external_review"]["fallbacks"]] == [
        "opus",
        "p/m#high",
    ]
    applied = migrate_external_config(tmp_path, True)
    assert Path(applied["backup"]).read_bytes() == original
    assert not migrate_external_config(tmp_path, True)["changed"]
    with pytest.raises(ValueError, match="external-migrate"):
        ExternalReviewConfig.model_validate(old["external_review"])


@pytest.mark.parametrize(
    "failure_type", ["authentication", "timeout", "model", "quota", "format", "provider"]
)
def test_failure_fallback_order_and_no_content_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_type: str
) -> None:
    root, batch = project(tmp_path)
    config = load_project(root)
    config.external_review.fallbacks = [
        reviewer("opencode-cli", "backup"),
        reviewer("claude-code", "last"),
    ]
    save_project(root, config)
    calls = []

    def invoke(item, *args):
        calls.append(item.id)
        if item.id == "primary":
            raise external.ExternalInvocationError(
                "invocation failed", 1, failure_type=failure_type
            )
        return response({"verdict": "inconclusive", "summary": "Unsure", "issues": []}, item)

    monkeypatch.setattr(external, "_invoke", invoke)
    monkeypatch.setattr(external, "_command_version", lambda _: "test-cli")
    status = external.run_external_review(root, batch)
    assert calls == ["primary", "backup"]
    assert status["recheck"]["required"] and not status["external_approvable"]
    external.run_external_review(root, batch)
    assert len(calls) == 2
    runs = read_jsonl(external._runs_path(root, batch), ExternalReviewRun)
    assert runs[1].fallback_of == runs[0].run_id
    assert not (root / ".littrans/external-assignments").exists()


def test_blind_recheck_adjudication_and_stale_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, batch = project(tmp_path)
    payload = {
        "verdict": "changes-requested",
        "summary": "Check meaning",
        "issues": [finding(root, batch)],
    }
    monkeypatch.setattr(external, "_invoke", lambda item, *args: response(payload, item))
    monkeypatch.setattr(external, "_command_version", lambda _: "test-cli")
    status = external.run_external_review(root, batch)
    assert workflow_next(root)["stage"] == "external-recheck"
    task = create_task(root, "external-recheck", batch_ids=[batch], host="codex")
    task_path = Path(task["handoff"]).parent / "task.json"
    packet = read_json(task_path)["packet"]
    assert "ORIGINAL_OPINION_SENTINEL" not in (root / packet["packet_path"]).read_text(
        encoding="utf-8"
    )
    with pytest.raises(ValueError, match="subagent"):
        claim_task(root, task["task_id"], "fresh-reviewer", "fresh-session")
    claim_task(root, task["task_id"], "fresh-reviewer", "subagent")
    result = tmp_path / "result.json"
    write_json(result, {"verdict": "accepted", "summary": "No defect found", "issues": []})
    receive_task(root, task["task_id"], result)
    assert receive_task(root, task["task_id"], result)["replayed"]
    assert workflow_next(root)["stage"] == "external-adjudicate"
    assert not external.external_review_status(root, batch)["external_approvable"]
    decision = {
        "run_id": status["primary"]["run_id"],
        "task_id": task["task_id"],
        "verdict": "accepted",
        "reason": "Both source and target preserve the tested qualification.",
        "issues": {
            status["primary"]["issue_ids"][0]: {
                "action": "reject",
                "reason": "Exact spans preserve meaning.",
            }
        },
    }
    decision_path = tmp_path / "decision.json"
    write_json(decision_path, decision)
    assert adjudicate_recheck(root, batch, decision_path)["external_approvable"]
    assert workflow_next(root)["stage"] == "external-approve"
    _submit(root, batch, suffix="变更")
    assert not external.external_review_status(root, batch)["external_approvable"]
    with pytest.raises(ValueError, match="current"):
        adjudicate_recheck(root, batch, decision_path)


def test_stale_recheck_cannot_be_imported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, batch = project(tmp_path)
    monkeypatch.setattr(
        external,
        "_invoke",
        lambda item, *args: response(
            {"verdict": "inconclusive", "summary": "Unsure", "issues": []}, item
        ),
    )
    monkeypatch.setattr(external, "_command_version", lambda _: "test-cli")
    external.run_external_review(root, batch)
    task = create_task(root, "external-recheck", batch_ids=[batch])
    claim_task(root, task["task_id"], "new-host-reviewer", "subagent")
    _submit(root, batch, suffix="变更")
    result = tmp_path / "result.json"
    write_json(result, {"verdict": "accepted", "summary": "No defects", "issues": []})
    with pytest.raises(ValueError, match="current|changed|stale"):
        receive_task(root, task["task_id"], result)


def test_cli_metadata_cannot_be_replaced_by_requested_model(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="verified"):
        codex_metadata([{"type": "thread.started", "thread_id": "not-a-session"}], tmp_path)
    with pytest.raises(RuntimeError, match="verified"):
        opencode_metadata(
            {
                "info": {"id": "session", "model": {"id": "requested"}},
                "messages": [{"type": "assistant", "content": [{"text": "I am requested"}]}],
            },
            "session",
        )
    metadata = opencode_metadata(
        {
            "info": {"id": "session"},
            "messages": [
                {
                    "type": "assistant",
                    "model": {"id": "served", "providerID": "p", "variant": "custom"},
                }
            ],
        },
        "session",
    )
    assert metadata["actual_model"] == "p/served" and metadata["actual_effort"] == "custom"


def test_old_host_and_second_opinion_records_never_satisfy_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, batch = project(tmp_path)
    monkeypatch.setattr(
        external,
        "_invoke",
        lambda item, *args: response(
            {"verdict": "accepted", "summary": "No defects", "issues": []}, item
        ),
    )
    monkeypatch.setattr(external, "_command_version", lambda _: "test-cli")
    status = external.run_external_review(root, batch)
    run = ExternalReviewRun.model_validate(status["primary"])
    assert external._is_cli_run(run)
    assert not external._is_cli_run(run.model_copy(update={"cli_version": "cursor-host-subagent"}))
    assert not external._is_cli_run(run.model_copy(update={"role": "second-opinion"}))
    assert not external._is_cli_run(run.model_copy(update={"execution": None, "cli_version": None}))
    assert external._is_cli_run(run.model_copy(update={"execution": None}))


def test_coordinator_cannot_skip_or_accept_unresolved_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, batch = project(tmp_path)
    payload = {
        "verdict": "changes-requested",
        "summary": "Check the translation meaning",
        "issues": [finding(root, batch)],
    }
    monkeypatch.setattr(external, "_invoke", lambda item, *args: response(payload, item))
    monkeypatch.setattr(external, "_command_version", lambda _: "test-cli")
    external.run_external_review(root, batch)
    decision_path = tmp_path / "decision.json"
    write_json(decision_path, {})
    with pytest.raises(ValueError, match="independent recheck"):
        adjudicate_recheck(root, batch, decision_path)
    task = create_task(root, "external-recheck", batch_ids=[batch])
    # Repeated task creation is idempotent and preserves the image evidence.
    assert create_task(root, "external-recheck", batch_ids=[batch])["task_id"] == task["task_id"]
    envelope = read_json(Path(task["handoff"]).parent / "task.json")
    assert any(path.endswith(".png") for path in envelope["inputs"])
    claim_task(root, task["task_id"], "independent", "subagent")
    result = tmp_path / "result.json"
    write_json(result, payload)
    receive_task(root, task["task_id"], result)
    status = external.external_review_status(root, batch)
    decision = status["recheck"]["decision_template"]
    assert len(decision["issues"]) == 2
    decision["verdict"] = "accepted"
    decision["reason"] = "Both reviewers agree."
    for item in decision["issues"].values():
        item.update(action="accept", reason="The source supports this defect.")
    write_json(decision_path, decision)
    with pytest.raises(ValueError, match="substantive findings"):
        adjudicate_recheck(root, batch, decision_path)
    decision["verdict"] = "changes-requested"
    write_json(decision_path, decision)
    status = adjudicate_recheck(root, batch, decision_path)
    assert status["recheck"]["complete"] and not status["external_approvable"]
    assert workflow_next(root)["stage"] == "revise"


def test_unverified_cli_cannot_be_replaced_by_host_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, batch = project(tmp_path)

    def fail(*args):
        raise external.ExternalInvocationError(
            "actual model could not be verified", 1, failure_type="model"
        )

    monkeypatch.setattr(external, "_invoke", fail)
    monkeypatch.setattr(external, "_command_version", lambda _: "test-cli")
    status = external.run_external_review(root, batch)
    assert not status["recheck"]["required"] and not status["external_approvable"]
    with pytest.raises(ValueError, match="failed or unverified"):
        create_task(root, "external-recheck", batch_ids=[batch])


def test_account_eligibility_and_variant_failures_are_classified() -> None:
    assert (
        external._classify_invocation_failure("Eligibility check failed: account is not eligible")
        == "authentication"
    )
    assert external._classify_invocation_failure("unknown variant custom") == "model"


def test_incremental_failure_gives_replacement_a_full_packet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from littrans.models import TranslationRecord
    from littrans.storage import write_jsonl

    root, manifests = _make_project(tmp_path, pages=6, max_words=10000)
    assert len(manifests) == 1
    batch = manifests[0].batch_id
    config = load_project(root)
    config.external_review = ExternalReviewConfig(reviewer=reviewer(), fallbacks=[reviewer("opencode-cli", "backup")])
    save_project(root, config)
    _submit(root, batch)
    _audit_and_approve(root, batch)
    payload = {"verdict": "accepted", "summary": "No substantive defects found", "issues": []}
    monkeypatch.setattr(external, "_invoke", lambda item, *args: response(payload, item))
    monkeypatch.setattr(external, "_command_version", lambda _: "test-cli")
    assert external.run_external_review(root, batch)["external_approvable"]
    records = read_jsonl(root / "translations/current.jsonl", TranslationRecord)
    records[0] = records[0].model_copy(update={"target_text": records[0].target_text + "修订", "revision": 2})
    write_jsonl(root / "translations/current.jsonl", records)
    _audit_and_approve(root, batch)
    preview = external.run_external_review(root, batch, dry_run=True)
    assert [call["scope"] for call in preview["calls"]] == ["incremental", "full"]
    calls = []
    def invoke(item, packet, work, evidence):
        calls.append((item.id, set(evidence)))
        if item.id == "primary":
            raise external.ExternalInvocationError("quota exhausted", 1, failure_type="quota")
        return response(payload, item)
    monkeypatch.setattr(external, "_invoke", invoke)
    status = external.run_external_review(root, batch)
    assert status["external_approvable"] and status["primary"]["scope"] == "full"
    assert len(calls[0][1]) < len(calls[1][1]) == 6


@pytest.mark.skipif(
    os.environ.get("LITTRANS_LIVE_EXTERNAL") != "1", reason="Explicit paid CLI smoke only"
)
@pytest.mark.parametrize("driver", ["codex-cli", "opencode-cli", "claude-code"])
def test_live_external_cli(driver: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session-sentinel")
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-session-sentinel")
    root, batch = project(tmp_path)
    config = load_project(root)
    config.external_review = ExternalReviewConfig(reviewer=reviewer(driver))
    save_project(root, config)
    # Faithful translation of the synthetic vocabulary fixture, with preserved repetition.
    _submit(
        root, batch, target_text=" ".join(["架构 框架 绑定 属性 控件 布局 事件 样式 模板"] * 14)
    )
    _audit_and_approve(root, batch)
    status = external.run_external_review(root, batch)
    assert status["primary"]["success"], status["primary"]["summary"]
    assert status["primary"]["model_verified"]
    assert status["primary"]["effort"] == "low"
    if driver != "claude-code":
        assert status["primary"]["actual_effort"] == "low"
    assert status["primary"]["response_path"]
    assert status["primary"]["verdict"] in {"accepted", "changes-requested", "inconclusive"}
    assert status["external_approvable"] == (
        status["verdict"] == "accepted" and not status["open_substantive_issues"]
    )


@pytest.mark.skipif(
    os.environ.get("LITTRANS_LIVE_ANTIGRAVITY") != "1",
    reason="Explicit authentication-failure smoke only",
)
def test_live_antigravity_authentication_fallback(tmp_path: Path) -> None:
    root, batch = project(tmp_path)
    config = load_project(root)
    config.external_review = ExternalReviewConfig(
        reviewer=ExternalReviewerConfig(
            id="agy", driver="antigravity", command="agy", model="gemini-3.1-pro", effort="high"
        ),
        fallbacks=[reviewer()],
    )
    save_project(root, config)
    status = external.run_external_review(root, batch)
    runs = read_jsonl(external._runs_path(root, batch), ExternalReviewRun)
    assert len(runs) == 2
    assert runs[0].failure_type == "authentication" and not runs[0].success
    assert runs[1].fallback_of == runs[0].run_id and runs[1].success
    assert status["primary"]["driver"] == "codex-cli"


def test_external_process_environment_detaches_parent_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from littrans.external_cli import invocation_environment

    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CODEX_THREAD_ID", "parent")
    monkeypatch.setenv("CURSOR_AGENT", "parent")
    monkeypatch.setenv("LITTRANS_TEST_KEEP", "kept")
    env = invocation_environment()
    assert all(name not in env for name in ("CLAUDECODE", "CODEX_THREAD_ID", "CURSOR_AGENT"))
    assert env["LITTRANS_TEST_KEEP"] == "kept" and os.environ["CLAUDECODE"] == "1"
