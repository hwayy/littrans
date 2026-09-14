"""Build a wheel and clean, installable plugin archive without switching an installation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/literature-translation"


def build(output: Path) -> dict[str, object]:
    output = output.resolve()
    if output == PLUGIN or PLUGIN in output.parents:
        raise ValueError("Build output must be outside the plugin source directory")
    output.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PLUGIN / "src")
    subprocess.run([sys.executable, str(ROOT / "scripts/validate_release.py")], env=env, check=True)
    subprocess.run([sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(output)], cwd=PLUGIN, check=True)
    wheel = output / f"littrans-{version}-py3-none-any.whl"
    allowed = {".claude-plugin", ".codex-plugin", ".cursor-plugin", "agents", "profiles", "references", "schemas", "scripts", "skills", "src"}
    standalone = {"pyproject.toml", "README.md", "MIGRATING.md"}
    archive = output / f"literature-translation-{version}.zip"
    source_hashes = {}
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(PLUGIN.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(PLUGIN)
            if relative.parts[0] not in allowed and relative.as_posix() not in standalone:
                continue
            if any(part.startswith(("__pycache__", ".pytest", ".mypy", ".ruff")) for part in relative.parts) or path.suffix == ".pyc":
                continue
            data = path.read_bytes()
            source_hashes[relative.as_posix()] = hashlib.sha256(data).hexdigest()
            info = zipfile.ZipInfo("literature-translation/" + relative.as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data)
        info = zipfile.ZipInfo("literature-translation/LICENSE", (2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        bundle.writestr(info, (ROOT / "LICENSE").read_bytes())
    report = {"version": version, "stable_installation_changed": False,
              "files": {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
                        for p in (wheel, archive)}, "plugin_source_files": source_hashes}
    (output / "build-manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    result = build(parser.parse_args().output)
    print(json.dumps({k: v for k, v in result.items() if k != "plugin_source_files"}, indent=2))
