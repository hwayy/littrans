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
        "QODER_PRODUCT_ID",
        "QODER_CONFIG_DIR",
        "QODERCN_CLI",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def isolate_layout_runtime(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
                           tmp_path_factory: pytest.TempPathFactory) -> None:
    """Keep the suite deterministic and fast on machines with the layout detector installed.

    Every unpatched `prepare_source` would otherwise start a torch subprocess and hash the
    detector weights. Synthetic fixtures review their own regions, so the detector adds nothing
    to them; tests that need the real runtime opt in with `@pytest.mark.layout_runtime`.
    The cache root is a temporary directory (so no test adopts the machine's legacy AppData
    weights), and the process never counts as a packaged app, whichever client runs the suite.
    """
    if request.node.get_closest_marker("layout_runtime"):
        return
    from littrans import layout_detector, layout_runtime

    monkeypatch.setenv("LITTRANS_LAYOUT_PYTHON", "littrans-tests-no-layout-runtime")
    monkeypatch.setenv("LITTRANS_LAYOUT_MODEL", "littrans-tests-no-layout-runtime")
    monkeypatch.setenv("LITTRANS_CACHE_DIR", str(tmp_path_factory.mktemp("littrans-cache")))
    monkeypatch.setattr(layout_detector, "packaged_app", lambda: False)
    monkeypatch.setattr(layout_runtime, "packaged_app", lambda: False)
