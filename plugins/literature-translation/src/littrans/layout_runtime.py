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
from littrans.layout_detector import (
    REDIRECTED_REASON,
    cache_root,
    layout_cache_root,
    legacy_cache_root,
    model_weight_hashes,
    packaged_app,
    ready_weight_hashes,
    redirected,
    runtime_paths,
    runtime_readiness_error,
    write_ready_marker,
)

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


def _run_to_stderr(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    """Run an installer step with its progress on stderr: the command's stdout is its JSON."""
    return subprocess.run(command, check=False, stdout=_stderr_target())


def _stderr_target() -> int:
    """The process's own stderr descriptor for a child's stdout, or DEVNULL without one."""
    try:
        return sys.__stderr__.fileno() if sys.__stderr__ is not None else subprocess.DEVNULL
    except (AttributeError, OSError, ValueError):
        return subprocess.DEVNULL


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


# Run in the detector interpreter: who it really is, and whether the stack the worker needs
# imports. Package metadata alone does not show a torch built for another Python.
STACK_PROBE = """
import importlib.metadata as m, json, sys
from pathlib import Path
info = {"version": "%d.%d.%d" % sys.version_info[:3], "executable": sys.executable,
        "base_executable": getattr(sys, "_base_executable", sys.executable),
        "resolved_executable": str(Path(sys.executable).resolve()),
        "mineru_version": None, "import_error": None}
try:
    info["mineru_version"] = m.version("mineru")
except m.PackageNotFoundError:
    pass
try:
    import torch, torchvision
    from mineru.model.layout.pp_doclayoutv2 import PPDocLayoutV2LayoutModel
except BaseException as exc:
    info["import_error"] = "%s: %s" % (type(exc).__name__, exc)
print(json.dumps(info))
"""


def _probe_stack(python: Path) -> dict[str, Any]:
    """The detector interpreter's own identity and import check; ValueError when it cannot run."""
    try:
        result = _run([str(python), "-c", STACK_PROBE], timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"layout interpreter does not run: {exc}") from exc
    lines = result.stdout.strip().splitlines()
    try:
        payload = json.loads(lines[-1]) if not result.returncode and lines else None
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise ValueError(f"layout interpreter does not run (exit {result.returncode}): {result.stderr.strip()[-500:]}")
    return payload


def _pyvenv(python: Path) -> dict[str, str]:
    """The ``home`` and ``version`` a virtual environment recorded when it was created."""
    config = python.parent.parent / "pyvenv.cfg"
    try:
        lines = config.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values = dict(line.split("=", 1) for line in lines if "=" in line)
    recorded = {key.strip(): value.strip() for key, value in values.items()}
    version = recorded.get("version") or recorded.get("version_info")
    return {key: value for key, value in (("home", recorded.get("home")), ("version", version)) if value}


def _minor(version: str) -> str:
    return ".".join(version.split(".")[:2])


def _identity_problem(python: Path, identity: dict[str, Any]) -> str | None:
    """Why the interpreter is not the environment its path names, or None."""
    if any(redirected(Path(path)) for path in (python, identity["resolved_executable"], identity["base_executable"]) if path):
        return REDIRECTED_REASON
    # The recorded `home` is reported, not compared: a Store or aliased base interpreter
    # legitimately runs from another directory than the one pyvenv.cfg names.
    recorded_version = identity.get("pyvenv_version")
    if recorded_version and _minor(recorded_version) != _minor(identity["version"]):
        return (f"environment identity mismatch: pyvenv.cfg records Python {recorded_version}, "
                f"the interpreter runs {identity['version']}; run littrans layout install --repair")
    return None


def layout_runtime_status() -> dict[str, Any]:
    """Describe the detector runtime without changing it.

    Beyond package metadata, weights and the smoke receipt, the interpreter itself is asked
    who it is and must import the detector stack: a receipt only proves the state at install.
    """
    python, model = runtime_paths()
    legacy = legacy_cache_root()
    status: dict[str, Any] = {
        "python": str(python) if python else None,
        "model": str(model) if model else None,
        "cache_root": str(cache_root()),
        "mineru_version": None,
        "identity": None,
        "packaged_app": packaged_app(),
        "ok": False,
        "reason": None,
        "install_command": "littrans layout install",
    }
    if legacy is not None and (legacy / "layout").is_dir():
        # Builds before 0.7.1 kept the runtime in AppData; `layout install` adopts its
        # verified weights. The directory is never removed automatically.
        status["legacy_cache"] = str(legacy)
    if not python or not python.is_file():
        status["reason"] = "layout interpreter missing"
        return status
    try:
        probe = _probe_stack(python)
    except ValueError as exc:
        status["reason"] = f"{exc}; run littrans layout install --repair"
        return status
    version = probe.get("mineru_version")
    status["mineru_version"] = version
    pyvenv = _pyvenv(python)
    identity = {"configured_python": str(python.absolute()), "resolved_python": str(python.resolve()),
                "pyvenv_home": pyvenv.get("home"), "pyvenv_version": pyvenv.get("version"),
                "version": str(probe.get("version")), "executable": probe.get("executable"),
                "resolved_executable": probe.get("resolved_executable"),
                "base_executable": probe.get("base_executable"), "import_error": probe.get("import_error")}
    status["identity"] = identity
    # Identity first: in a redirected view every later answer describes the wrong environment.
    problem = _identity_problem(python, identity)
    if problem:
        status["reason"] = problem
        return status
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
    if identity["import_error"]:
        status["reason"] = (f"detector stack does not import ({identity['import_error']}); "
                            "run littrans layout install --repair")
        return status
    status["ok"] = True
    return status


def _pip(python: Path, *args: str) -> None:
    command = [str(python), "-m", "pip", "install", "--disable-pip-version-check", *args]
    result = _run_to_stderr(command)
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
    result = _run_to_stderr([str(python), "-c", script])
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


def _redirection_risk(cache: Path) -> str | None:
    """Why installing into ``cache`` from this process would write a redirected copy, or None."""
    if redirected(cache) or redirected(cache / "venv"):
        return REDIRECTED_REASON
    if packaged_app():
        appdata = [Path(os.environ[name]) for name in ("LOCALAPPDATA", "APPDATA") if os.environ.get(name)]
        if any(cache.absolute().is_relative_to(root.absolute()) for root in appdata):
            return REDIRECTED_REASON
    return None


def _adopt_legacy_weights(model: Path) -> Path | None:
    """Copy the weights of the pre-0.7.1 AppData cache when its ready receipt verifies them."""
    legacy = legacy_cache_root()
    if legacy is None:
        return None
    source = legacy / "layout" / MODEL_NAME
    recorded = ready_weight_hashes(legacy / "layout" / "venv" / READY_MARKER)
    try:
        if not source.is_dir() or recorded is None or model_weight_hashes(source) != recorded:
            return None
    except OSError:
        return None
    if model.exists():
        shutil.rmtree(model)
    model.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, model)
    return source


def install_layout_runtime(python: Path | None = None, force: bool = False,
                           model_source: str = "huggingface", repair: bool = False) -> dict[str, Any]:
    """Create the isolated layout environment and fetch the detector weights.

    ``repair`` recreates the environment only and keeps weights the ready receipt verifies;
    ``force`` recreates both. Weights of the pre-0.7.1 AppData cache that its own receipt
    verifies are copied instead of downloaded. A packaged app whose view of the cache is
    redirected is refused: whatever it wrote would land in its private copy, or corrupt
    the real environment in place.
    """
    if model_source not in MODEL_REPOS:
        raise ValueError("model source must be huggingface or modelscope")
    if force and repair:
        raise ValueError("--force already recreates the environment; pass --force or --repair, not both")
    if os.environ.get("LITTRANS_LAYOUT_PYTHON") or os.environ.get("LITTRANS_LAYOUT_MODEL"):
        raise RuntimeError(
            "LITTRANS_LAYOUT_PYTHON/LITTRANS_LAYOUT_MODEL point at an externally managed runtime; "
            "unset them before installing the managed one"
        )
    cache = layout_cache_root()
    risk = _redirection_risk(cache)
    if risk:
        raise RuntimeError(risk)
    current = layout_runtime_status()
    if current["ok"] and not force and not repair:
        return {**current, "installed": False, "message": "layout runtime already available"}
    environment = cache / "venv"
    model = cache / MODEL_NAME
    venv_python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    marker = environment / READY_MARKER
    recorded = ready_weight_hashes(marker)
    had_marker = marker.is_file()
    files_exist = all((model / name).is_file() for name in ("config.json", "model.safetensors"))
    adopted = None
    if not files_exist and not force:
        adopted = _adopt_legacy_weights(model)
        if adopted is not None:
            files_exist, had_marker, recorded = True, True, None
    current_hashes = model_weight_hashes(model) if model.is_dir() else {}
    hashes_mismatch = recorded is not None and recorded != current_hashes
    need_download = force or not files_exist or hashes_mismatch or not had_marker
    marker.unlink(missing_ok=True)
    if (force or repair) and environment.exists():
        shutil.rmtree(environment)
    if not venv_python.is_file():
        base = select_base_python(python)
        environment.parent.mkdir(parents=True, exist_ok=True)
        result = _run_to_stderr([str(base), "-m", "venv", str(environment)])
        if result.returncode or not venv_python.is_file():
            raise RuntimeError(f"failed to create the layout environment with {base}")
    _pip(venv_python, "--upgrade", "pip")
    _pip(venv_python, "torch", "torchvision", "--index-url", TORCH_CPU_INDEX)
    _pip(venv_python, *LAYOUT_PACKAGES)
    if need_download:
        _download_weights(venv_python, model, model_source)
    smoke = _smoke_test(venv_python, model)
    if smoke.get("status") != "ok":
        raise RuntimeError("layout smoke test did not report ok")
    write_ready_marker(marker, model)
    return {**layout_runtime_status(), "installed": True, "smoke_test": smoke,
            "model_source": model_source, "weights_downloaded": need_download, "repaired": repair,
            "adopted_legacy_weights": str(adopted) if adopted is not None else None}
