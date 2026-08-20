from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from littrans import external_review
from littrans.models import (
    ExternalReviewerConfig,
    PromptDelivery,
)


def _reviewer(driver: str = "claude-code") -> ExternalReviewerConfig:
    if driver == "cursor-cli":
        return ExternalReviewerConfig(
            id="reviewer",
            driver=driver,
            command="reviewer-cli",
            model="cursor-grok-4.6-high-fast",
            fallbacks=[{"model": "claude-sonnet-5-high"}, {"model": "auto"}],
        )
    return ExternalReviewerConfig(
        id="reviewer",
        driver=driver,
        command="reviewer-cli",
        model="claude-sonnet-5" if driver == "claude-code" else "gemini-3.1-pro",
        effort="high",
        fast=False if driver == "claude-code" else None,
    )


def _cursor_stream(
    payload: dict[str, object],
    model: str = "Cursor Grok 4.6 High Fast",
    *,
    success: bool = True,
) -> str:
    events = [
        {
            "type": "system",
            "subtype": "init",
            "model": model,
            "session_id": "session",
        },
        {"type": "assistant", "message": {"role": "assistant"}},
        {
            "type": "result",
            "subtype": "success" if success else "error",
            "is_error": not success,
            "result": json.dumps(payload),
            "usage": {
                "inputTokens": 10,
                "outputTokens": 4,
                "cacheReadTokens": 3,
                "cacheWriteTokens": 2,
            },
        },
    ]
    return "\n".join(json.dumps(event) for event in events)


def _cursor_plan_stream(
    plan: str,
    model: str = "Cursor Grok 4.6 High Fast",
    *,
    completed: bool = True,
    tool_success: bool = True,
    outer_success: bool = True,
    final_result: str = "The review result was recorded in the plan.",
) -> str:
    create_plan: dict[str, object] = {
        "args": {"plan": plan},
    }
    if completed:
        create_plan["result"] = (
            {"success": {}, "planUri": ""}
            if tool_success
            else {"error": {"message": "plan rejected"}}
        )
    events: list[dict[str, object]] = [
        {
            "type": "system",
            "subtype": "init",
            "model": model,
            "session_id": "session",
        },
        {
            "type": "tool_call",
            "subtype": "completed" if completed else "started",
            "call_id": "plan-call",
            "tool_call": {"createPlanToolCall": create_plan},
        },
        {
            "type": "result",
            "subtype": "success" if outer_success else "error",
            "is_error": not outer_success,
            "result": final_result,
            "usage": {
                "inputTokens": 12,
                "outputTokens": 5,
                "cacheReadTokens": 4,
                "cacheWriteTokens": 3,
            },
        },
    ]
    return "\n".join(json.dumps(event) for event in events)


def test_cursor_command_and_stream_protocol() -> None:
    reviewer = _reviewer("cursor-cli")
    command = external_review.build_cursor_command(reviewer, "review")
    assert command == [
        "reviewer-cli",
        "--print",
        "--output-format",
        "stream-json",
        "--mode",
        "plan",
        "--trust",
        "--model",
        "cursor-grok-4.6-high-fast",
        "review",
    ]
    assert "--sandbox" not in command
    payload = {
        "verdict": "accepted",
        "summary": "No substantive defects found.",
        "issues": [],
    }
    raw = _cursor_stream(payload)
    parsed, actual = external_review._parse_cursor(raw)
    usage, cost = external_review._review_usage(raw, reviewer.driver)
    assert parsed == payload
    assert actual == "Cursor Grok 4.6 High Fast"
    assert external_review._cursor_model_matches(reviewer.model, actual)
    assert usage.model_dump() == {
        "input_tokens": 10,
        "cache_creation_input_tokens": 2,
        "cache_read_input_tokens": 3,
        "output_tokens": 4,
        "provider_turns": 1,
    }
    assert cost is None


def test_cursor_plan_mode_parses_accepted_create_plan_result() -> None:
    payload = {
        "verdict": "accepted",
        "summary": "No substantive defects found.",
        "issues": [],
    }
    raw = _cursor_plan_stream(
        "# Review result\n\n```json\n"
        + json.dumps(payload)
        + "\n```\n"
    )

    parsed, actual = external_review._parse_cursor(raw)

    assert parsed == payload
    assert actual == "Cursor Grok 4.6 High Fast"


def test_cursor_plan_mode_parses_changes_requested_with_evidence() -> None:
    issue = {
        "unit_id": "u1",
        "severity": "minor",
        "type": "meaning",
        "source_span": "source span",
        "target_span": "目标片段",
        "explanation": "The target loses a technical distinction.",
        "suggested_revision": "修订后的目标片段",
        "confidence": 0.9,
    }
    payload = {
        "verdict": "changes-requested",
        "summary": "One substantive defect requires correction.",
        "issues": [issue],
    }

    parsed, _ = external_review._parse_cursor(
        _cursor_plan_stream(json.dumps(payload, ensure_ascii=False))
    )
    external_review._validate_issue_evidence(
        parsed, {"u1": ("full source span text", "完整目标片段文本")}
    )

    assert parsed == payload


@pytest.mark.parametrize(
    ("plan", "completed", "tool_success"),
    [
        ("Review complete; no defects were found.", True, True),
        (
            '```json\n{"verdict":"accepted","summary":"ok","issues":[]}\n```\n'
            '```json\n{"verdict":"accepted","summary":"also ok","issues":[]}\n```',
            True,
            True,
        ),
        (
            '```json\n{"verdict":"accepted","summary":"ok","issues":[]}\n```\n'
            '{"verdict":"accepted","summary":"duplicate","issues":[]}',
            True,
            True,
        ),
        ('{"verdict":"accepted","summary":"ok","issues":[]}', False, True),
        ('{"verdict":"accepted","summary":"ok","issues":[]}', True, False),
    ],
)
def test_cursor_plan_mode_rejects_unsafe_plan_fallbacks(
    plan: str, completed: bool, tool_success: bool
) -> None:
    with pytest.raises(ValueError):
        external_review._parse_cursor(
            _cursor_plan_stream(
                plan,
                completed=completed,
                tool_success=tool_success,
            )
        )


def test_cursor_plan_mode_requires_successful_outer_result() -> None:
    payload = {
        "verdict": "accepted",
        "summary": "No substantive defects found.",
        "issues": [],
    }
    with pytest.raises(RuntimeError, match="error result"):
        external_review._parse_cursor(
            _cursor_plan_stream(json.dumps(payload), outer_success=False)
        )


def test_cursor_plan_mode_rejects_schema_invalid_payload() -> None:
    with pytest.raises(ValueError):
        external_review._parse_cursor(
            _cursor_plan_stream('{"verdict":"accepted","issues":[]}')
        )


def test_cursor_prefers_valid_final_result_over_create_plan() -> None:
    final_payload = {
        "verdict": "accepted",
        "summary": "The final result is authoritative.",
        "issues": [],
    }
    plan_payload = {
        "verdict": "inconclusive",
        "summary": "This plan must not replace a valid final result.",
        "issues": [],
    }
    raw = _cursor_plan_stream(
        json.dumps(plan_payload), final_result=json.dumps(final_payload)
    )

    parsed, _ = external_review._parse_cursor(raw)

    assert parsed == final_payload


def test_cursor_does_not_hide_invalid_final_schema_with_create_plan() -> None:
    plan_payload = {
        "verdict": "accepted",
        "summary": "The plan is valid but must not replace the final result.",
        "issues": [],
    }
    raw = _cursor_plan_stream(
        json.dumps(plan_payload),
        final_result=json.dumps({"verdict": "accepted"}),
    )

    with pytest.raises(ValueError):
        external_review._parse_cursor(raw)


def test_cursor_auto_prefatory_fenced_final_result_remains_supported() -> None:
    payload = {
        "verdict": "accepted",
        "summary": "Auto completed the review.",
        "issues": [],
    }
    raw = _cursor_stream(payload, model="Auto")
    events = [json.loads(line) for line in raw.splitlines()]
    events[-1]["result"] = (
        "Review complete.\n```json\n"
        + json.dumps(payload)
        + "\n```\n"
    )

    parsed, actual = external_review._parse_cursor(
        "\n".join(json.dumps(event) for event in events)
    )

    assert parsed == payload
    assert actual == "Auto"


@pytest.mark.parametrize(
    ("requested", "actual"),
    [
        ("cursor-grok-4.6-high-fast", "Cursor Grok 4.6 High Fast"),
        ("claude-sonnet-5-high", "Sonnet 5 1M High"),
        ("auto", "Auto"),
    ],
)
def test_cursor_actual_model_identity(requested: str, actual: str) -> None:
    assert external_review._cursor_model_matches(requested, actual)


@pytest.mark.parametrize(
    ("requested", "actual"),
    [
        ("cursor-grok-4.6-high", "Grok"),
        ("cursor-grok-4.6-high", "Grok 4"),
        ("cursor-grok-4.6-high", "Cursor Grok 4.6 Low"),
        ("cursor-grok-4.6-high-fast", "Cursor Grok 4.6 High"),
    ],
)
def test_cursor_actual_model_identity_rejects_loose_matches(
    requested: str, actual: str
) -> None:
    assert not external_review._cursor_model_matches(requested, actual)


def test_cursor_requires_model_and_final_result_evidence() -> None:
    payload = {
        "verdict": "accepted",
        "summary": "No substantive defects found.",
        "issues": [],
    }
    with pytest.raises(ValueError, match="actual model"):
        external_review._parse_cursor(
            json.dumps({"type": "result", "subtype": "success", "result": json.dumps(payload)})
        )
    with pytest.raises(ValueError, match="final result"):
        external_review._parse_cursor(
            json.dumps({"type": "system", "subtype": "init", "model": "Auto"})
        )


def test_cursor_quota_fallback_crosses_independent_pools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = _reviewer("cursor-cli")
    packet = tmp_path / "review-packet.md"
    packet.write_text("Review packet.", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(external_review.shutil, "which", lambda command: command)
    models: list[str] = []

    def quota_then_success(
        command: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        model = command[command.index("--model") + 1]
        models.append(model)
        if model != "auto":
            pool = "first-party" if model.startswith("cursor-") else "third-party"
            return subprocess.CompletedProcess(
                command, 1, "", f"Cursor {pool} usage limit reached"
            )
        payload = {
            "verdict": "accepted",
            "summary": "No substantive defects found.",
            "issues": [],
        }
        return subprocess.CompletedProcess(command, 0, _cursor_stream(payload, "Auto"), "")

    monkeypatch.setattr(external_review.subprocess, "run", quota_then_success)
    result = external_review._invoke(reviewer, packet, work, {})
    assert models == [
        "cursor-grok-4.6-high-fast",
        "claude-sonnet-5-high",
        "auto",
    ]
    assert result[2] == "auto"
    attempts = [
        json.loads(line)
        for line in (work / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [item["failure_type"] for item in attempts] == ["quota", "quota", None]
    assert [item["quota_pool"] for item in attempts] == [
        "cursor-first-party",
        "cursor-third-party",
        "cursor-first-party",
    ]


def test_cursor_create_plan_does_not_bypass_actual_model_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = ExternalReviewerConfig(
        id="reviewer",
        driver="cursor-cli",
        command="reviewer-cli",
        model="cursor-grok-4.6-high-fast",
    )
    packet = tmp_path / "review-packet.md"
    packet.write_text("Review packet.", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    payload = {
        "verdict": "accepted",
        "summary": "No substantive defects found.",
        "issues": [],
    }
    monkeypatch.setattr(external_review.shutil, "which", lambda command: command)
    monkeypatch.setattr(
        external_review.subprocess,
        "run",
        lambda command, *args, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            _cursor_plan_stream(json.dumps(payload), model="Unexpected Model"),
            "",
        ),
    )

    with pytest.raises(
        external_review.ExternalInvocationError,
        match="actual model could not be verified",
    ):
        external_review._invoke(reviewer, packet, work, {})


def test_cursor_create_plan_does_not_bypass_issue_evidence_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = ExternalReviewerConfig(
        id="reviewer",
        driver="cursor-cli",
        command="reviewer-cli",
        model="cursor-grok-4.6-high-fast",
    )
    packet = tmp_path / "review-packet.md"
    packet.write_text("Review packet.", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    payload = {
        "verdict": "changes-requested",
        "summary": "One issue requires correction.",
        "issues": [
            {
                "unit_id": "u1",
                "severity": "minor",
                "type": "meaning",
                "source_span": "fabricated source span",
                "target_span": "真实目标",
                "explanation": "The source span does not exist in the packet.",
                "suggested_revision": "修订目标",
                "confidence": 0.9,
            }
        ],
    }
    calls = 0

    def same_invalid_evidence(
        command: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            command,
            0,
            _cursor_plan_stream(json.dumps(payload, ensure_ascii=False)),
            "",
        )

    monkeypatch.setattr(external_review.shutil, "which", lambda command: command)
    monkeypatch.setattr(external_review.subprocess, "run", same_invalid_evidence)

    with pytest.raises(external_review.ExternalInvocationError, match="source_span"):
        external_review._invoke(
            reviewer,
            packet,
            work,
            {"u1": ("real source", "真实目标")},
        )
    assert calls == 2


def test_cursor_valid_create_plan_avoids_redundant_targeted_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = ExternalReviewerConfig(
        id="reviewer",
        driver="cursor-cli",
        command="reviewer-cli",
        model="cursor-grok-4.6-high-fast",
    )
    packet = tmp_path / "review-packet.md"
    packet.write_text("Review packet.", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    payload = {
        "verdict": "accepted",
        "summary": "No substantive defects found.",
        "issues": [],
    }
    calls = 0

    def plan_success(
        command: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            command,
            0,
            _cursor_plan_stream(
                "# Review JSON\n```json\n"
                + json.dumps(payload)
                + "\n```"
            ),
            "",
        )

    monkeypatch.setattr(external_review.shutil, "which", lambda command: command)
    monkeypatch.setattr(external_review.subprocess, "run", plan_success)

    result = external_review._invoke(reviewer, packet, work, {})

    assert result[0] == payload
    assert calls == 1
    telemetry = [
        json.loads(line)
        for line in (work / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert telemetry[0]["success"] is True


def test_cursor_targeted_repair_uses_only_recognized_plan_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = ExternalReviewerConfig(
        id="reviewer",
        driver="cursor-cli",
        command="reviewer-cli",
        model="cursor-grok-4.6-high-fast",
    )
    packet = tmp_path / "review-packet.md"
    packet.write_text("Review packet.", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    valid = {
        "verdict": "accepted",
        "summary": "No substantive defects found.",
        "issues": [],
    }
    prompts: list[str] = []

    def invalid_then_valid(
        command: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        prompt_path = Path(command[-1].split("`", 2)[1])
        prompts.append(prompt_path.read_text(encoding="utf-8"))
        payload = {"verdict": "accepted"} if len(prompts) == 1 else valid
        return subprocess.CompletedProcess(
            command,
            0,
            _cursor_plan_stream(json.dumps(payload)),
            "",
        )

    monkeypatch.setattr(external_review.shutil, "which", lambda command: command)
    monkeypatch.setattr(external_review.subprocess, "run", invalid_then_valid)

    result = external_review._invoke(reviewer, packet, work, {})

    assert result[0] == valid
    assert len(prompts) == 2
    assert 'Previous response:\n{"verdict": "accepted"}' in prompts[1]
    assert "createPlanToolCall" not in prompts[1]
    assert '"type": "result"' not in prompts[1]


@pytest.mark.parametrize(
    ("outer_turns", "usage_turns", "expected"),
    [(3, 9, 3), (None, 4, 4), (None, None, 0)],
)
def test_antigravity_usage_counts_envelope_turns_first(
    outer_turns: int | None, usage_turns: int | None, expected: int
) -> None:
    envelope: dict[str, object] = {"usage": {"input_tokens": 10}}
    if outer_turns is not None:
        envelope["num_turns"] = outer_turns
    if usage_turns is not None:
        assert isinstance(envelope["usage"], dict)
        envelope["usage"]["provider_turns"] = usage_turns

    usage, _ = external_review._review_usage(
        json.dumps(envelope), _reviewer("antigravity").driver
    )

    assert usage.provider_turns == expected


def test_minimal_claude_protocol_is_explicitly_quality_gated() -> None:
    reviewer = _reviewer()
    legacy = external_review.build_claude_command(
        reviewer, "review", minimal_file_protocol=False
    )
    optimized = external_review.build_claude_command(
        reviewer, "review", minimal_file_protocol=True
    )

    assert "--system-prompt" not in legacy
    assert optimized[optimized.index("--system-prompt") + 1] == (
        external_review.CLAUDE_MINIMAL_SYSTEM_PROMPT
    )
    assert external_review.CLAUDE_MINIMAL_FILE_PROTOCOL_ENABLED is False


def test_external_result_rejects_a_noop_suggested_revision() -> None:
    payload = {
        "verdict": "changes-requested",
        "summary": "A localized wording defect requires correction.",
        "issues": [
            {
                "unit_id": "u1",
                "severity": "minor",
                "type": "style",
                "source_span": "source",
                "target_span": "换言之",
                "explanation": "The wording should be changed.",
                "suggested_revision": "换言之",
                "confidence": 0.9,
            }
        ],
    }

    with pytest.raises(
        ValueError, match="suggested_revision must differ from target_span"
    ):
        external_review._validate_result(payload)


def test_external_evidence_rejects_noop_against_full_effective_target() -> None:
    payload = {
        "verdict": "changes-requested",
        "summary": "A localized wording defect requires correction.",
        "issues": [
            {
                "unit_id": "u1",
                "severity": "minor",
                "type": "style",
                "source_span": "source",
                "target_span": "换言之",
                "explanation": "The wording should be changed.",
                "suggested_revision": "换言之。",
                "confidence": 0.9,
            }
        ],
    }

    with pytest.raises(ValueError, match="renderer-effective target"):
        external_review._validate_issue_evidence(
            payload, {"u1": ("source", "换言之。")}
        )


def test_format_retry_uses_previous_output_not_the_full_packet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = _reviewer()
    packet = tmp_path / "review-packet.md"
    packet.write_text("VERY LARGE PACKET BODY", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(external_review.shutil, "which", lambda command: command)
    commands: list[list[str]] = []

    def invalid_then_valid(
        command: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        payload = (
            {"verdict": "accepted"}
            if len(commands) == 1
            else {
                "verdict": "accepted",
                "summary": "No substantive defects found.",
                "issues": [],
            }
        )
        raw = json.dumps(
            {
                "structured_output": payload,
                "modelUsage": {reviewer.model: {"inputTokens": 1}},
                "fast_mode_state": "off",
            }
        )
        return subprocess.CompletedProcess(command, 0, raw, "")

    monkeypatch.setattr(external_review.subprocess, "run", invalid_then_valid)

    result = external_review._invoke(reviewer, packet, work, {})

    assert result[0]["verdict"] == "accepted"
    assert "Repair the previous review response" in commands[1][-1]
    assert "VERY LARGE PACKET BODY" not in commands[1][-1]
    telemetry = [
        json.loads(line)
        for line in (work / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert telemetry[0]["failure_type"] == "format"
    assert telemetry[0]["targeted_repair_scheduled"] is True
    assert telemetry[1]["success"] is True


def test_attempt_telemetry_persists_raw_response_and_failure_type(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    work = tmp_path / "work"
    work.mkdir()
    (root / "reviews").mkdir(parents=True)
    external_review._record_local_attempt(
        work,
        {
            "attempt": 1,
            "reviewer_id": "reviewer",
            "driver": "antigravity",
            "requested_model": "gemini-3.1-pro",
            "effort": "high",
            "prompt_delivery": PromptDelivery.FILE.value,
            "duration_seconds": 1.25,
            "success": False,
            "failure_type": "authentication",
            "error": "token expired",
            "usage": {
                "input_tokens": 1,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 2,
                "provider_turns": 1,
            },
            "cost_usd": None,
        },
        "provider raw response",
    )

    external_review._persist_attempt_telemetry(root, "batch-1", "run-1", work)

    record = json.loads(
        (root / "reviews" / "batch-1.external-attempts.jsonl").read_text(
            encoding="utf-8"
        )
    )
    assert record["failure_type"] == "authentication"
    assert record["run_id"] == "run-1"
    assert (root / record["raw_response_path"]).read_text(encoding="utf-8") == (
        "provider raw response"
    )


def test_provider_lock_serializes_same_driver_but_not_different_drivers(
    tmp_path: Path,
) -> None:
    active = 0
    maximum = 0
    guard = threading.Lock()

    def enter(reviewer: ExternalReviewerConfig) -> None:
        nonlocal active, maximum
        with external_review._provider_call_lock(tmp_path, reviewer, 2):
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with guard:
                active -= 1

    same = [threading.Thread(target=enter, args=(_reviewer(),)) for _ in range(2)]
    for thread in same:
        thread.start()
    for thread in same:
        thread.join()
    assert maximum == 1

    maximum = 0
    different = [
        threading.Thread(target=enter, args=(_reviewer("claude-code"),)),
        threading.Thread(target=enter, args=(_reviewer("antigravity"),)),
    ]
    for thread in different:
        thread.start()
    for thread in different:
        thread.join()
    assert maximum == 2


def test_provider_lock_reclaims_owner_from_terminated_process(tmp_path: Path) -> None:
    reviewer = _reviewer()
    lock_dir = (
        tmp_path / ".littrans" / "external-provider-locks" / "claude-code.lock"
    )
    lock_dir.mkdir(parents=True)
    (lock_dir / "owner.json").write_text(
        json.dumps(
            {
                "pid": 2_147_483_647,
                "token": "terminated-owner",
                "created_at": 0,
            }
        ),
        encoding="utf-8",
    )

    active = 0
    maximum = 0
    guard = threading.Lock()
    errors: list[BaseException] = []

    def enter() -> None:
        nonlocal active, maximum
        try:
            with external_review._provider_call_lock(tmp_path, reviewer, 2):
                with guard:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.05)
                with guard:
                    active -= 1
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=enter) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert maximum == 1
    assert not lock_dir.exists()


def test_provider_lock_reclaims_half_written_owner_directory(tmp_path: Path) -> None:
    reviewer = _reviewer()
    lock_root = tmp_path / ".littrans" / "external-provider-locks"
    lock_dir = lock_root / "claude-code.lock"
    lock_dir.mkdir(parents=True)
    (lock_dir / ".owner.json.interrupted").write_text("partial", encoding="utf-8")
    os.utime(lock_dir, (0, 0))

    with external_review._provider_call_lock(tmp_path, reviewer, 1):
        assert (lock_dir / "owner.json").is_file()

    assert not lock_dir.exists()
    assert not list(lock_root.glob(".claude-code.lock.stale-*"))
