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


def layout_cache_root() -> Path:
    """Managed location of the isolated layout environment and its weights."""
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "littrans/layout"


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


def runtime_readiness_error(python: Path, model: Path, cache: Path | None = None) -> str | None:
    """Require the managed smoke receipt, including redirected cache components."""
    cache = layout_cache_root() if cache is None else cache
    managed = any(path.absolute().is_relative_to(cache.absolute())
                  or path.resolve().is_relative_to(cache.resolve()) for path in (python, model))
    if managed and not (cache / "venv" / READY_MARKER).is_file():
        return "managed layout smoke test not completed; run littrans layout install"
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


def detect_layout(images: list[Path], output: Path) -> dict[str, Any]:
    """Return per-image pixel boxes, retaining inline formulas, or explicit unavailable state."""
    python, model = runtime_paths()
    if not python or not python.is_file() or not model or not model.is_dir():
        return {"status": "unavailable", "reason": "Layout runtime missing: run `littrans layout install` (MinerU 3.4.5, PP-DocLayoutV2) or configure LITTRANS_LAYOUT_PYTHON and LITTRANS_LAYOUT_MODEL; native evidence requires full visual region review.", "pages": {}}
    readiness_error = runtime_readiness_error(python, model)
    if readiness_error:
        return {"status": "unavailable", "reason": readiness_error, "pages": {}}
    worker = Path(__file__).with_name("layout_worker.py")
    try:
        runtime = _runtime_identity(python)
        worker_sha = sha256_file(worker)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "reason": str(exc), "pages": {}}
    weights = {str(p.relative_to(model)): sha256_file(p) for p in sorted(model.rglob("*")) if p.is_file()}
    request = {"images": [str(p.resolve()) for p in images], "image_sha256": {str(p.resolve()): sha256_file(p) for p in images}, "model": str(model.resolve()), "weights": weights}
    request.update(runtime=runtime, worker_sha256=worker_sha)
    request["fingerprint"] = sha256_text(str(request))
    if output.is_file():
        try:
            existing = read_json(output)
        except (OSError, ValueError):
            existing = {}
        if (existing.get("fingerprint") == request["fingerprint"] and existing.get("status") == "ok"
                and isinstance(existing.get("pages"), dict) and set(existing["pages"]) == set(request["images"])):
            return existing
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
        if (payload.get("status") != "ok" or payload.get("fingerprint") != request["fingerprint"]
                or not isinstance(payload.get("pages"), dict) or set(payload["pages"]) != set(request["images"])):
            raise ValueError("layout worker returned incomplete or stale output")
        payload["elapsed_seconds"] = time.monotonic() - started
        write_json(output, payload)
        return payload
    except (OSError, ValueError, subprocess.TimeoutExpired, RuntimeError) as exc:
        payload = {"status": "unavailable", "reason": str(exc), "pages": {}, "elapsed_seconds": time.monotonic() - started}
        write_json(output, payload)
        return payload
