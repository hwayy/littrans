from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path


def _cache_root() -> Path:
    # Same rule as littrans.layout_detector.cache_root. Windows stays out of AppData: an
    # MSIX-packaged client (Codex) sees a redirected, merged copy of it.
    if os.environ.get("LITTRANS_CACHE_DIR"):
        return Path(os.environ["LITTRANS_CACHE_DIR"])
    if os.name == "nt":
        return Path.home() / ".littrans"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "littrans"


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _environment_key(plugin_root: Path) -> str:
    material = (plugin_root / "pyproject.toml").read_bytes()
    digest = hashlib.sha256(material).hexdigest()[:12]
    return f"py{sys.version_info.major}{sys.version_info.minor}-{digest}"


def ensure_runtime(plugin_root: Path) -> Path:
    environment = _cache_root() / _environment_key(plugin_root)
    python = _venv_python(environment)
    marker = environment / ".littrans-ready"
    if python.is_file() and marker.is_file():
        return python
    environment.parent.mkdir(parents=True, exist_ok=True)
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            str(plugin_root),
        ],
        check=True,
        stdout=_stderr_target(),
    )
    marker.write_text("ready\n", encoding="utf-8")
    return python


def _stderr_target() -> int:
    """Where a setup step's output goes: stderr, because the CLI's stdout carries its JSON."""
    try:
        return sys.__stderr__.fileno() if sys.__stderr__ is not None else subprocess.DEVNULL
    except (AttributeError, OSError, ValueError):
        return subprocess.DEVNULL


def main() -> None:
    plugin_root = Path(__file__).resolve().parents[1]
    python = ensure_runtime(plugin_root)
    command = [str(python), str(plugin_root / "scripts" / "littrans.py"), *sys.argv[1:]]
    raise SystemExit(subprocess.run(command, check=False).returncode)


if __name__ == "__main__":
    main()
