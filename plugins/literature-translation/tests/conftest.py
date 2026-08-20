from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_coordination_host(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CURSOR_TRACE_ID",
        "CURSOR_AGENT",
        "CURSOR_INVOKED_AS",
        "CODEX_THREAD_ID",
        "CODEX_TASK_ID",
        "CODEX_CI",
    ):
        monkeypatch.delenv(name, raising=False)
