"""Isolated CPU layout-only adapter. Never imports any formula decoder."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from littrans.storage import read_json, sha256_file, sha256_text, write_json

READY_MARKER = ".littrans-layout-ready"


def model_weight_hashes(model: Path) -> dict[str, str]:
    """Content-address every file in a detector snapshot."""
    return {str(path.relative_to(model)): sha256_file(path)
            for path in sorted(model.rglob("*")) if path.is_file()}


def ready_weight_hashes(marker: Path) -> dict[str, str] | None:
    """Read the smoke-test receipt's weight hashes, or None for a legacy marker."""
    if not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    weights = payload.get("weights") if isinstance(payload, dict) else None
    if not isinstance(weights, dict) or not weights:
        return None
    if any(not isinstance(name, str) or not isinstance(digest, str) or len(digest) != 64
           for name, digest in weights.items()):
        return None
    return weights


def write_ready_marker(marker: Path, model: Path) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"weights": model_weight_hashes(model)}, sort_keys=True) + "\n",
                      encoding="utf-8")


def cache_root() -> Path:
    """Where the CLI and layout environments live — the same rule as ``scripts/bootstrap.py``.

    ``LITTRANS_CACHE_DIR`` when set; otherwise ``%USERPROFILE%/.littrans`` on Windows and
    ``$XDG_CACHE_HOME/littrans`` (default ``~/.cache``) elsewhere. Windows keeps out of
    AppData because an MSIX-packaged client (Codex) is shown a redirected copy of it merged
    with the real directory, so two hosts would see, and repair, different runtimes.
    """
    if os.environ.get("LITTRANS_CACHE_DIR"):
        return Path(os.environ["LITTRANS_CACHE_DIR"])
    if os.name == "nt":
        return Path.home() / ".littrans"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "littrans"


def layout_cache_root() -> Path:
    """Managed location of the isolated layout environment and its weights."""
    return cache_root() / "layout"


def legacy_cache_root() -> Path | None:
    """The cache builds before 0.7.1 used on Windows (``%LOCALAPPDATA%/littrans``), or None."""
    if os.name != "nt" or os.environ.get("LITTRANS_CACHE_DIR") or not os.environ.get("LOCALAPPDATA"):
        return None
    return Path(os.environ["LOCALAPPDATA"]) / "littrans"


APPMODEL_ERROR_NO_PACKAGE = 15700
REDIRECTED_REASON = (
    "AppData redirection: this packaged app sees a private copy of the layout runtime, not the one "
    "other hosts use; run LitTrans from a non-packaged shell, or set LITTRANS_CACHE_DIR outside AppData"
)


def packaged_app() -> bool:
    """Whether this process runs with Windows package identity (an MSIX app such as Codex).

    Such a process has AppData redirected to a private copy merged with the real one.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        length = ctypes.c_uint32(0)
        result = ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)  # type: ignore[attr-defined,unused-ignore]
    except (AttributeError, OSError):
        return False
    return bool(result != APPMODEL_ERROR_NO_PACKAGE)


def in_package_cache(path: Path) -> bool:
    """Whether ``path`` names a location inside a packaged app's ``Packages/<id>/LocalCache``."""
    parts = [part.lower() for part in path.parts]
    return any(parts[i] == "packages" and parts[i + 2] == "localcache" for i in range(len(parts) - 2))


def redirected(path: Path) -> bool:
    """Whether ``path`` resolves into a packaged app's private ``Packages/<id>/LocalCache`` copy.

    Pass the path as configured: an already-resolved path names the private copy directly
    and reads as not redirected.
    """
    try:
        return in_package_cache(path.resolve()) and not in_package_cache(path.absolute())
    except OSError:
        return False


def runtime_paths() -> tuple[Path | None, Path | None]:
    interpreter = os.environ.get("LITTRANS_LAYOUT_PYTHON")
    weight = os.environ.get("LITTRANS_LAYOUT_MODEL")
    cache = layout_cache_root()
    if not interpreter:
        candidates = [cache / "venv/Scripts/python.exe", cache / "venv/bin/python"]
        interpreter = next((str(p) for p in candidates if p.is_file()), None)
    if not weight and (cache / "PP-DocLayoutV2/config.json").is_file():
        weight = str(cache / "PP-DocLayoutV2")
    return Path(interpreter) if interpreter else None, Path(weight) if weight else None


def runtime_readiness_error(python: Path, model: Path, cache: Path | None = None,
                            weights: dict[str, str] | None = None) -> str | None:
    """Require the managed smoke receipt, including redirected cache components.

    ``weights`` lets a caller that already hashed the snapshot skip a second pass
    over the detector weights.
    """
    cache = layout_cache_root() if cache is None else cache
    managed = any(path.absolute().is_relative_to(cache.absolute())
                  or path.resolve().is_relative_to(cache.resolve()) for path in (python, model))
    if not managed:
        return None
    marker = cache / "venv" / READY_MARKER
    if not marker.is_file():
        return "managed layout smoke test not completed; run littrans layout install"
    recorded = ready_weight_hashes(marker)
    if recorded is None:
        return "managed layout ready receipt lacks weight hashes; run littrans layout install"
    try:
        current = model_weight_hashes(model) if weights is None else weights
    except OSError:
        return "managed layout weights are unreadable; run littrans layout install"
    if recorded != current:
        return "managed layout weights do not match the ready receipt; run littrans layout install"
    return None


def _runtime_identity(python: Path) -> dict[str, Any]:
    probe = (
        "import sys,json,importlib.metadata as m; "
        "print(json.dumps({'python':sys.version,'executable':sys.executable,"
        "'prefix':sys.prefix,'packages':sorted((d.metadata['Name'],d.version) "
        "for d in m.distributions())}))"
    )
    result = subprocess.run([str(python), "-c", probe], capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    if result.returncode:
        raise ValueError("layout runtime identity probe failed")
    identity = json.loads(result.stdout)
    if not isinstance(identity, dict) or not identity.get("python") or not identity.get("packages"):
        raise ValueError("layout runtime identity probe returned invalid metadata")
    return {"configured_python": str(python.absolute()), "resolved_python": str(python.resolve()),
            "interpreter_sha256": sha256_file(python), **identity}


def layout_page_items(layout: dict[str, Any], image: Path, image_sha256: str | None) -> list[dict[str, Any]] | None:
    """The detector's boxes for one page image, or None when the result does not hold them.

    Results are keyed by image content (SHA-256), so a project tree keeps its layout
    evidence wherever it is moved or cloned. A result written before content keys named
    the image by its absolute path at detection time; it is still read by that path or,
    once the tree has moved, by the image file name, which is unique within a project.
    """
    pages = layout.get("pages")
    if not isinstance(pages, dict):
        return None
    for key in (image_sha256, str(image.resolve())):
        if key in pages and isinstance(pages[key], list):
            return list(pages[key])
    # Legacy keys may come from a different OS; native Path parsing cannot split
    # Windows backslashes on POSIX.
    legacy = [items for key, items in pages.items()
              if key.replace("\\", "/").rsplit("/", 1)[-1] == image.name and isinstance(items, list)]
    return list(legacy[0]) if len(legacy) == 1 else None


def _content_keyed(pages: Any, image_sha256: dict[str, str]) -> dict[str, Any] | None:
    """Re-key a worker result from the request's image paths to image content, or None."""
    if not isinstance(pages, dict) or set(pages) != set(image_sha256):
        return None
    if any(not isinstance(value, list) for value in pages.values()):
        return None
    return {image_sha256[name]: items for name, items in pages.items()}


def layout_result_path(store: Path, fingerprint: str) -> Path:
    """Where a detection result lives: one file per fingerprint, never overwritten by another."""
    return store / f"{fingerprint}.json"


def detect_layout(images: list[Path], store: Path) -> dict[str, Any]:
    """Return per-image pixel boxes, retaining inline formulas, or explicit unavailable state.

    ``pages`` is keyed by image SHA-256 and the fingerprint binds image content, detector
    weights, runtime and worker, never a path of the project, so the same tree detects and
    replays the same result under any root. Results are content-addressed inside ``store``
    (``<fingerprint>.json`` with its ``.request.json`` and ``.log``): a rerun on the same
    runtime reuses its file, a rerun on another runtime writes a new one, and a result a
    page ledger already records is never overwritten (``path`` names the file).
    """
    python, model = runtime_paths()
    if not python or not python.is_file() or not model or not model.is_dir():
        return {"status": "unavailable", "reason": "Layout runtime missing: run `littrans layout install` (MinerU 3.4.5, PP-DocLayoutV2) or configure LITTRANS_LAYOUT_PYTHON and LITTRANS_LAYOUT_MODEL; native evidence requires full visual region review.", "pages": {}}
    try:
        weights = model_weight_hashes(model)
    except OSError:
        return {"status": "unavailable", "reason": "managed layout weights are unreadable; run littrans layout install", "pages": {}}
    readiness_error = runtime_readiness_error(python, model, weights=weights)
    if readiness_error:
        return {"status": "unavailable", "reason": readiness_error, "pages": {}}
    # The configured path, not its resolved form: a resolved path is already the private
    # copy and no longer shows the redirection.
    if redirected(python):
        return {"status": "unavailable", "reason": REDIRECTED_REASON, "pages": {}}
    worker = Path(__file__).with_name("layout_worker.py")
    try:
        runtime = _runtime_identity(python)
        worker_sha = sha256_file(worker)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "reason": str(exc), "pages": {}}
    image_sha256 = {str(p.resolve()): sha256_file(p) for p in images}
    request: dict[str, Any] = {"images": list(image_sha256), "image_sha256": image_sha256, "model": str(model.resolve()), "weights": weights}
    request.update(runtime=runtime, worker_sha256=worker_sha)
    identity = {"image_sha256": sorted(set(image_sha256.values())), "weights": weights, "runtime": runtime, "worker_sha256": worker_sha}
    request["fingerprint"] = sha256_text(json.dumps(identity, sort_keys=True))
    expected_keys = set(image_sha256.values())
    output = layout_result_path(store, request["fingerprint"])
    if output.is_file():
        try:
            existing = read_json(output)
        except (OSError, ValueError):
            existing = {}
        if (existing.get("fingerprint") == request["fingerprint"] and existing.get("status") == "ok"
                and isinstance(existing.get("pages"), dict) and set(existing["pages"]) == expected_keys
                and all(isinstance(value, list) for value in existing["pages"].values())):
            return {**existing, "path": str(output)}
    request_path = output.with_suffix(".request.json")
    write_json(request_path, request)
    env = os.environ.copy()
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", MINERU_DEVICE_MODE="cpu", OMP_NUM_THREADS="4", MKL_NUM_THREADS="4", PYTHONIOENCODING="utf-8")
    started = time.monotonic()
    try:
        result = subprocess.run([str(python), str(worker), str(request_path), str(output)], env=env, capture_output=True, text=True, encoding="utf-8", timeout=1800)
        output.with_suffix(".log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"layout worker exit {result.returncode}; see {output.with_suffix('.log')}")
        payload = read_json(output)
        pages = _content_keyed(payload.get("pages"), image_sha256) if payload.get("status") == "ok" and payload.get("fingerprint") == request["fingerprint"] else None
        if pages is None:
            raise ValueError("layout worker returned incomplete or stale output")
        # Image names, not paths: the result is part of the project record and must not
        # carry the directory of the host that ran the detector.
        payload.update(pages=pages, images={Path(name).name: digest for name, digest in image_sha256.items()},
                       elapsed_seconds=time.monotonic() - started)
        write_json(output, payload)
        return {**payload, "path": str(output)}
    except (OSError, ValueError, subprocess.TimeoutExpired, RuntimeError) as exc:
        payload = {"status": "unavailable", "reason": str(exc), "pages": {}, "elapsed_seconds": time.monotonic() - started}
        write_json(output, payload)
        return {**payload, "path": str(output)}
