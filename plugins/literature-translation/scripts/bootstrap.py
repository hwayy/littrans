from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path


def _cache_root() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "littrans"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "littrans"


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
    )
    marker.write_text("ready\n", encoding="utf-8")
    return python


def main() -> None:
    plugin_root = Path(__file__).resolve().parents[1]
    python = ensure_runtime(plugin_root)
    command = [str(python), str(plugin_root / "scripts" / "littrans.py"), *sys.argv[1:]]
    raise SystemExit(subprocess.run(command, check=False).returncode)


if __name__ == "__main__":
    main()
