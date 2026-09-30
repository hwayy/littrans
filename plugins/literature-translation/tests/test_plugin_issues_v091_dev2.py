"""0.9.1-dev.2: identities of the plugin's own files do not depend on checkout line endings.

A Windows checkout had written tracked files with CRLF although Git stores LF. Hosts install
from the working tree byte for byte, so the layout worker's hash (part of every layout
fingerprint) and the build digest differed from a clean checkout of the same commit. Both now
hash LF text, and release validation refuses a working tree with such files.
"""
from __future__ import annotations

import runpy
import shutil
import subprocess
from pathlib import Path

import pytest

import littrans
from littrans import build_info
from littrans.layout_detector import worker_sha256
from littrans.storage import lf_bytes, sha256_text

PLUGIN = Path(__file__).resolve().parents[1]
PACKAGE = PLUGIN / "src" / "littrans"
REPO = PLUGIN.parents[1]


def _crlf(path: Path) -> None:
    path.write_bytes(lf_bytes(path).replace(b"\n", b"\r\n"))


def test_lf_bytes_only_rewrites_crlf(tmp_path: Path) -> None:
    path = tmp_path / "text.md"
    path.write_bytes(b"a\r\nb\nc\rd\r\n")
    assert lf_bytes(path) == b"a\nb\nc\rd\n"


def test_layout_worker_identity_ignores_line_endings(tmp_path: Path) -> None:
    worker = PACKAGE / "layout_worker.py"
    lf, crlf = tmp_path / "lf.py", tmp_path / "crlf.py"
    lf.write_bytes(lf_bytes(worker))
    crlf.write_bytes(lf_bytes(worker))
    _crlf(crlf)
    assert crlf.read_bytes() != lf.read_bytes()
    assert worker_sha256(crlf) == worker_sha256(lf) == worker_sha256(worker)
    assert worker_sha256(lf) == sha256_text(lf.read_text(encoding="utf-8"))
    edited = tmp_path / "edited.py"
    edited.write_bytes(lf_bytes(worker) + b"# changed\n")
    assert worker_sha256(edited) != worker_sha256(lf)


def _package_copy(root: Path, crlf: bool, monkeypatch: pytest.MonkeyPatch) -> str:
    package = root / "src" / "littrans"
    shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(PLUGIN / "profiles", root / "profiles")
    for path in [*package.rglob("*"), *(root / "profiles").rglob("*")]:
        if path.is_file():
            path.write_bytes(lf_bytes(path))
            if crlf:
                _crlf(path)
    monkeypatch.setattr(littrans, "__file__", str(package / "__init__.py"))
    return build_info.build_digest.__wrapped__()


def test_build_digest_ignores_line_endings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lf = _package_copy(tmp_path / "lf", False, monkeypatch)
    crlf = _package_copy(tmp_path / "crlf", True, monkeypatch)
    assert lf == crlf
    (tmp_path / "crlf/src/littrans/cli.py").write_bytes(b"# changed\r\n")
    assert build_info.build_digest.__wrapped__() != lf


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_release_validation_lists_crlf_checkouts(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")
    (tmp_path / "clean.md").write_bytes(b"clean\n")
    (tmp_path / "windows.md").write_bytes(b"windows\n")
    (tmp_path / "mixed.md").write_bytes(b"one\ntwo\n")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "init")
    (tmp_path / "windows.md").write_bytes(b"windows\r\n")  # an editor rewrote it; Git still sees no change
    (tmp_path / "mixed.md").write_bytes(b"one\r\ntwo\n")  # a partial rewrite leaves mixed endings
    check = runpy.run_path(str(REPO / "scripts" / "validate_release.py"))["crlf_working_tree_files"]
    check.__globals__["ROOT"] = tmp_path
    assert check() == ["mixed.md", "windows.md"]
