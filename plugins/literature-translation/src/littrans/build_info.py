"""Identity of the plugin build that wrote an artifact.

Extraction output depends on the exact source tree, not only on the released version:
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
    """Digest of every Python module in the running ``littrans`` package."""
    package = Path(littrans.__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def build_identity() -> dict[str, Any]:
    return {"plugin_version": littrans.__version__, "build_digest": build_digest(), "generated_at": utc_now()}
