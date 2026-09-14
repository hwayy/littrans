from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "layout_runtime: run against the locally installed MinerU layout detector instead of the "
        "deterministic unavailable stub",
    )


@pytest.fixture(autouse=True)
def isolate_coordination_host(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CURSOR_TRACE_ID",
        "CURSOR_AGENT",
        "CURSOR_INVOKED_AS",
        "CODEX_THREAD_ID",
        "CODEX_TASK_ID",
        "CODEX_CI",
        "CLAUDECODE",
        "CLAUDE_CODE_SESSION_ID",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def isolate_layout_runtime(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite deterministic and fast on machines with the layout detector installed.

    Every unpatched `prepare_source` would otherwise start a torch subprocess and hash the
    detector weights. Synthetic fixtures review their own regions, so the detector adds nothing
    to them; tests that need the real runtime opt in with `@pytest.mark.layout_runtime`.
    """
    if request.node.get_closest_marker("layout_runtime"):
        return
    monkeypatch.setenv("LITTRANS_LAYOUT_PYTHON", "littrans-tests-no-layout-runtime")
    monkeypatch.setenv("LITTRANS_LAYOUT_MODEL", "littrans-tests-no-layout-runtime")
