"""Provision and inspect the isolated CPU layout runtime (MinerU + PP-DocLayoutV2)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from littrans.layout_detector import READY_MARKER as READY_MARKER
from littrans.layout_detector import layout_cache_root, runtime_paths, runtime_readiness_error

MINERU_VERSION = "3.4.5"
MODEL_NAME = "PP-DocLayoutV2"
MODEL_REPOS = {
    "huggingface": "opendatalab/PDF-Extract-Kit-1.0",
    "modelscope": "OpenDataLab/PDF-Extract-Kit-1.0",
}
MODEL_RELATIVE_PATH = f"models/Layout/{MODEL_NAME}"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
LAYOUT_PACKAGES = (
    f"mineru=={MINERU_VERSION}",
    "transformers>=4.57.3,<5",
    "safetensors>=0.4.0,<1",
)
SUPPORTED_BASE_VERSIONS = ((3, 13), (3, 12), (3, 11), (3, 10))


def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kwargs)


def _interpreter_version(python: Path) -> tuple[int, int] | None:
    result = _run([str(python), "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"])
    if result.returncode:
        return None
    try:
        major, minor = (int(v) for v in result.stdout.split())
    except ValueError:
        return None
    return major, minor


def select_base_python(explicit: Path | None = None) -> Path:
    """Choose a Python 3.10-3.13 interpreter for the MinerU environment."""
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    elif os.environ.get("LITTRANS_LAYOUT_BASE_PYTHON"):
        candidates.append(Path(os.environ["LITTRANS_LAYOUT_BASE_PYTHON"]))
    else:
        for major, minor in SUPPORTED_BASE_VERSIONS:
            if os.name == "nt":
                launcher = shutil.which("py")
                if launcher:
                    probe = _run([launcher, f"-{major}.{minor}", "-c", "import sys; print(sys.executable)"])
                    if not probe.returncode and probe.stdout.strip():
                        candidates.append(Path(probe.stdout.strip()))
            found = shutil.which(f"python{major}.{minor}")
            if found:
                candidates.append(Path(found))
        base = Path(sys._base_executable if hasattr(sys, "_base_executable") else sys.executable)
        candidates.append(base)
    for candidate in candidates:
        version = _interpreter_version(candidate)
        if version in SUPPORTED_BASE_VERSIONS:
            return candidate
    raise RuntimeError(
        "No Python 3.10-3.13 interpreter found for the layout runtime; pass --python PATH "
        "or set LITTRANS_LAYOUT_BASE_PYTHON (MinerU 3.4.5 does not support other versions)"
    )


def layout_runtime_status() -> dict[str, Any]:
    """Describe the detector runtime without changing it."""
    python, model = runtime_paths()
    status: dict[str, Any] = {
        "python": str(python) if python else None,
        "model": str(model) if model else None,
        "mineru_version": None,
        "ok": False,
        "reason": None,
        "install_command": "littrans layout install",
    }
    if not python or not python.is_file():
        status["reason"] = "layout interpreter missing"
        return status
    result = _run([str(python), "-c", "import importlib.metadata as m; print(m.version('mineru'))"])
    version = result.stdout.strip() if not result.returncode else None
    status["mineru_version"] = version
    if version != MINERU_VERSION:
        status["reason"] = f"expected mineru=={MINERU_VERSION}, found {version or 'none'}"
        return status
    if not model or not (model / "config.json").is_file() or not (model / "model.safetensors").is_file():
        status["reason"] = f"{MODEL_NAME} weights missing"
        return status
    readiness_error = runtime_readiness_error(python, model, layout_cache_root())
    if readiness_error:
        status["reason"] = readiness_error
        return status
    status["ok"] = True
    return status


def _pip(python: Path, *args: str) -> None:
    command = [str(python), "-m", "pip", "install", "--disable-pip-version-check", *args]
    result = subprocess.run(command, check=False)
    if result.returncode:
        raise RuntimeError(f"pip failed ({result.returncode}): {' '.join(args)}")


def _download_weights(python: Path, destination: Path, model_source: str) -> None:
    repo = MODEL_REPOS[model_source]
    script = f"""
import shutil, sys
from pathlib import Path
if {model_source!r} == "huggingface":
    from huggingface_hub import snapshot_download
    root = snapshot_download({repo!r}, allow_patterns=[{MODEL_RELATIVE_PATH!r} + "/*"])
else:
    from modelscope import snapshot_download
    root = snapshot_download({repo!r}, allow_patterns=[{MODEL_RELATIVE_PATH!r} + "/*"])
source = Path(root) / {MODEL_RELATIVE_PATH!r}
if not (source / "config.json").is_file():
    sys.exit("downloaded snapshot lacks " + str(source))
target = Path({str(destination)!r})
if target.exists():
    shutil.rmtree(target)
shutil.copytree(source, target)
print(target)
"""
    result = subprocess.run([str(python), "-c", script], check=False)
    if result.returncode:
        raise RuntimeError(f"model download failed ({result.returncode}) from {model_source}")


def _smoke_test(python: Path, model: Path) -> dict[str, Any]:
    import pymupdf as fitz

    with tempfile.TemporaryDirectory() as tmp:
        image = Path(tmp) / "blank.png"
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 640, 900), False)
        pixmap.clear_with(255)
        pixmap.save(image)
        output = Path(tmp) / "layout.json"
        request = {"images": [str(image)], "image_sha256": {}, "model": str(model), "weights": {},
                   "fingerprint": "smoke"}
        request_path = Path(tmp) / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        worker = Path(__file__).with_name("layout_worker.py")
        env = os.environ.copy()
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", MINERU_DEVICE_MODE="cpu",
                   PYTHONIOENCODING="utf-8")
        result = _run([str(python), str(worker), str(request_path), str(output)], env=env, timeout=900)
        if result.returncode:
            raise RuntimeError(f"layout smoke test failed: {result.stderr[-2000:]}")
        payload = json.loads(output.read_text(encoding="utf-8"))
        return {"status": payload.get("status"), "version": payload.get("version")}


def install_layout_runtime(python: Path | None = None, force: bool = False,
                           model_source: str = "huggingface") -> dict[str, Any]:
    """Create the isolated layout environment and fetch the detector weights."""
    if model_source not in MODEL_REPOS:
        raise ValueError("model source must be huggingface or modelscope")
    if os.environ.get("LITTRANS_LAYOUT_PYTHON") or os.environ.get("LITTRANS_LAYOUT_MODEL"):
        raise RuntimeError(
            "LITTRANS_LAYOUT_PYTHON/LITTRANS_LAYOUT_MODEL point at an externally managed runtime; "
            "unset them before installing the managed one"
        )
    current = layout_runtime_status()
    if current["ok"] and not force:
        return {**current, "installed": False, "message": "layout runtime already available"}
    cache = layout_cache_root()
    environment = cache / "venv"
    model = cache / MODEL_NAME
    venv_python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    marker = environment / READY_MARKER
    marker.unlink(missing_ok=True)
    if force and environment.exists():
        shutil.rmtree(environment)
    if not venv_python.is_file():
        base = select_base_python(python)
        environment.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run([str(base), "-m", "venv", str(environment)], check=False)
        if result.returncode or not venv_python.is_file():
            raise RuntimeError(f"failed to create the layout environment with {base}")
    _pip(venv_python, "--upgrade", "pip")
    _pip(venv_python, "torch", "torchvision", "--index-url", TORCH_CPU_INDEX)
    _pip(venv_python, *LAYOUT_PACKAGES)
    if force or not all((model / name).is_file() for name in ("config.json", "model.safetensors")):
        _download_weights(venv_python, model, model_source)
    smoke = _smoke_test(venv_python, model)
    if smoke.get("status") != "ok":
        raise RuntimeError("layout smoke test did not report ok")
    (environment / READY_MARKER).write_text("ready\n", encoding="utf-8")
    return {**layout_runtime_status(), "installed": True, "smoke_test": smoke,
            "model_source": model_source}
