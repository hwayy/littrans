"""Identity of the plugin build that wrote an artifact.

Extraction output depends on the exact runtime tree, not only on the released version:
a development checkout keeps the version string while its behaviour moves. Artifacts
therefore record the version, a digest of the package sources and the time of writing.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import littrans
from littrans.models import utc_now


@lru_cache(maxsize=1)
def build_digest() -> str:
    """Digest Python modules and the resources that affect runtime behaviour."""
    package = Path(littrans.__file__).resolve().parent
    digest = hashlib.sha256()
    paths = {path.relative_to(package).as_posix(): path for path in package.rglob("*.py")}
    for directory in ("templates", "vendor"):
        paths.update(
            (path.relative_to(package).as_posix(), path)
            for path in (package / directory).rglob("*") if path.is_file()
        )
    profiles = package / "profiles"
    if not profiles.is_dir():
        profiles = package.parents[1] / "profiles"  # source checkout; wheel keeps them in-package
    paths.update(
        ("profiles/" + path.relative_to(profiles).as_posix(), path)
        for path in profiles.rglob("*") if path.is_file()
    )
    for name, path in sorted(paths.items()):
        if any(part.startswith(".") or part == "__pycache__" for part in Path(name).parts) or path.suffix in {".pyc", ".pyo", ".tmp", ".swp", ".bak"}:
            continue
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def build_identity() -> dict[str, Any]:
    return {"plugin_version": littrans.__version__, "build_digest": build_digest(), "generated_at": utc_now()}
