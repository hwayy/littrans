from __future__ import annotations

import json
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
    return ExternalReviewerConfig(
        id="reviewer",
        driver=driver,
        command="reviewer-cli",
        model="claude-sonnet-5" if driver == "claude-code" else "gemini-3.1-pro",
        effort="high",
        fast=False if driver == "claude-code" else None,
    )


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
