"""Isolated CPU layout-only adapter. Never imports any formula decoder."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from littrans.storage import read_json, sha256_file, sha256_text, write_json


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


def detect_layout(images: list[Path], output: Path) -> dict[str, Any]:
    """Return per-image pixel boxes, retaining inline formulas, or explicit unavailable state."""
    python, model = runtime_paths()
    if not python or not python.is_file() or not model or not model.is_dir():
        return {"status": "unavailable", "reason": "Layout runtime missing: run `littrans layout install` (MinerU 3.4.5, PP-DocLayoutV2) or configure LITTRANS_LAYOUT_PYTHON and LITTRANS_LAYOUT_MODEL; native evidence requires full visual region review.", "pages": {}}
    weights = {str(p.relative_to(model)): sha256_file(p) for p in sorted(model.rglob("*")) if p.is_file()}
    request = {"images": [str(p.resolve()) for p in images], "image_sha256": {str(p.resolve()): sha256_file(p) for p in images}, "model": str(model.resolve()), "weights": weights}
    request["fingerprint"] = sha256_text(str(request))
    if output.is_file():
        existing = read_json(output)
        if existing.get("fingerprint") == request["fingerprint"] and existing.get("status") == "ok":
            return existing
    request_path = output.with_suffix(".request.json")
    write_json(request_path, request)
    worker = Path(__file__).with_name("layout_worker.py")
    env = os.environ.copy()
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", MINERU_DEVICE_MODE="cpu", OMP_NUM_THREADS="4", MKL_NUM_THREADS="4", PYTHONIOENCODING="utf-8")
    started = time.monotonic()
    try:
        result = subprocess.run([str(python), str(worker), str(request_path), str(output)], env=env, capture_output=True, text=True, encoding="utf-8", timeout=1800)
        output.with_suffix(".log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"layout worker exit {result.returncode}; see {output.with_suffix('.log')}")
        payload = read_json(output)
        payload["elapsed_seconds"] = time.monotonic() - started
        write_json(output, payload)
        return payload
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        payload = {"status": "unavailable", "reason": str(exc), "pages": {}, "elapsed_seconds": time.monotonic() - started}
        write_json(output, payload)
        return payload
