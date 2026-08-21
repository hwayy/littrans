from __future__ import annotations

from pathlib import Path

import pytest
from test_efficiency_v4 import _make_project

from littrans.hosts import (
    LENS_REVIEWER_BATCH_MAX,
    WAVE_BATCH_SET_MAX,
    WAVE_LIMITS,
    detect_coordination_host,
    resolve_coordination_host,
    resolve_wave_limit,
)
from littrans.workflow import (
    _validate_batch_set,
    create_workflow_packet,
    workflow_next,
    workflow_status,
)


def test_codex_wave_limits_remain_three() -> None:
    assert WAVE_LIMITS["codex"].default == 3
    assert WAVE_LIMITS["codex"].maximum == 3
    assert LENS_REVIEWER_BATCH_MAX == 3
    assert resolve_wave_limit("codex", None) == 3
    with pytest.raises(ValueError, match="between 1 and 3 for host codex"):
        resolve_wave_limit("codex", 4)


def test_cursor_wave_limits_default_six_max_nine() -> None:
    assert WAVE_LIMITS["cursor"].default == 6
    assert WAVE_LIMITS["cursor"].maximum == 9
    assert WAVE_BATCH_SET_MAX == 9
    assert resolve_wave_limit("cursor", None) == 6
    assert resolve_wave_limit("cursor", 9) == 9
    with pytest.raises(ValueError, match="between 1 and 9 for host cursor"):
        resolve_wave_limit("cursor", 10)


def test_detect_cursor_host_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_TRACE_ID", "trace")
    assert detect_coordination_host() == "cursor"
    assert resolve_coordination_host("auto") == "cursor"
    assert resolve_coordination_host("codex") == "codex"
    with pytest.raises(ValueError, match="workflow host must be auto, codex, or cursor"):
        resolve_coordination_host("claude")


def test_workflow_next_auto_detects_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_TRACE_ID", "trace")
    root, manifests = _make_project(tmp_path, pages=9, max_words=100)
    result = workflow_next(root)
    assert result["host"] == "cursor"
    assert result["limit"] == 6
    assert len(result["batch_ids"]) == 6
    assert len(manifests) == 9


def test_mixed_host_signals_stay_on_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_TRACE_ID", "trace")
    monkeypatch.setenv("CODEX_THREAD_ID", "thread")
    assert detect_coordination_host() == "codex"


def test_workflow_next_selects_host_sized_waves(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, pages=9, max_words=100)
    assert len(manifests) == 9

    codex = workflow_next(root, host="codex")
    assert codex["host"] == "codex"
    assert codex["limit"] == 3
    assert len(codex["batch_ids"]) == 3

    cursor = workflow_next(root, host="cursor")
    assert cursor["host"] == "cursor"
    assert cursor["limit"] == 6
    assert len(cursor["batch_ids"]) == 6

    cursor_max = workflow_next(root, limit=9, host="cursor")
    assert len(cursor_max["batch_ids"]) == 9

    with pytest.raises(ValueError, match="between 1 and 3 for host codex"):
        workflow_next(root, limit=6, host="codex")


def test_status_and_packets_accept_a_cursor_wave(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, pages=6, max_words=100)
    batch_ids = [manifest.batch_id for manifest in manifests]
    assert len(batch_ids) == 6

    status = workflow_status(root, batch_ids)
    assert status["batch_ids"] == batch_ids
    packet = create_workflow_packet(root, "translate", batch_ids)
    assert not isinstance(packet, list)
    assert packet.batch_ids == batch_ids
    selected = _validate_batch_set(root, batch_ids)
    assert [manifest.batch_id for manifest in selected] == batch_ids

    oversized = [f"extra-{index}" for index in range(WAVE_BATCH_SET_MAX + 1)]
    with pytest.raises(ValueError, match="1 to 9 unique batch IDs"):
        workflow_status(root, oversized)
    with pytest.raises(ValueError, match="1 to 9 batch IDs"):
        create_workflow_packet(root, "translate", oversized)


def test_audit_packets_keep_the_three_batch_reviewer_cap(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, pages=4, max_words=100)
    batch_ids = [manifest.batch_id for manifest in manifests]
    assert len(batch_ids) == 4

    packet = create_workflow_packet(root, "translate", batch_ids)
    assert not isinstance(packet, list)
    assert packet.batch_ids == batch_ids
    for lens in ("all", "fidelity"):
        with pytest.raises(
            ValueError,
            match="audit packets require at most 3 consecutive batch IDs",
        ):
            create_workflow_packet(root, "audit", batch_ids, lens)
