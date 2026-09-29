"""Local isolation and private copies for reviewed, generated PDF templates."""
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from littrans import fidelity, layout_detector, layout_runtime
from littrans.storage import load_project, save_project


@contextmanager
def isolated_template(cache: Path) -> Iterator[None]:
    # Session fixtures run before the function-scoped autouse isolation.
    with pytest.MonkeyPatch.context() as patch:
        for name in (
            "CURSOR_TRACE_ID", "CURSOR_AGENT", "CURSOR_INVOKED_AS",
            "CODEX_THREAD_ID", "CODEX_TASK_ID", "CODEX_CI", "CLAUDECODE",
            "CLAUDE_CODE_SESSION_ID", "QODER_PRODUCT_ID", "QODER_CONFIG_DIR", "QODERCN_CLI",
        ):
            patch.delenv(name, raising=False)
        patch.setenv("CODEX_CI", "1")
        patch.setenv("LITTRANS_CACHE_DIR", str(cache))
        patch.setenv("LITTRANS_LAYOUT_PYTHON", "littrans-tests-no-layout-runtime")
        patch.setenv("LITTRANS_LAYOUT_MODEL", "littrans-tests-no-layout-runtime")
        patch.setattr(layout_detector, "packaged_app", lambda: False)
        patch.setattr(layout_runtime, "packaged_app", lambda: False)
        patch.setattr(fidelity, "detect_layout", lambda images, output: {
            "status": "unavailable", "reason": "Generated-source oracle", "pages": {},
        })
        yield


def copy_workflow_template(template: Path, destination: Path) -> Path:
    shutil.copy2(template / "oracle.pdf", destination / "oracle.pdf")
    root = destination / "project"
    shutil.copytree(template / "project", root)
    config = load_project(root)
    config.source_path = str(destination / "oracle.pdf")
    save_project(root, config)
    return root
