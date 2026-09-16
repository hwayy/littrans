"""Coordination-host detection, host-specific wave limits and host model defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast, get_args

import yaml

CoordinationHost = Literal["codex", "cursor", "claude", "qoder"]
COORDINATION_HOSTS: tuple[CoordinationHost, ...] = get_args(CoordinationHost)

CURSOR_ENV_SIGNALS = ("CURSOR_TRACE_ID", "CURSOR_AGENT", "CURSOR_INVOKED_AS")
CODEX_ENV_SIGNALS = ("CODEX_THREAD_ID", "CODEX_TASK_ID", "CODEX_CI")
CLAUDE_ENV_SIGNALS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID")
QODER_ENV_SIGNALS = ("QODER_PRODUCT_ID", "QODER_CONFIG_DIR", "QODERCN_CLI")

HOST_MODELS_FILE = "host-models.yaml"


@dataclass(frozen=True, slots=True)
class WaveLimit:
    default: int
    maximum: int


WAVE_LIMITS: dict[CoordinationHost, WaveLimit] = {
    "codex": WaveLimit(default=3, maximum=3),
    "cursor": WaveLimit(default=6, maximum=9),
    "claude": WaveLimit(default=3, maximum=6),
    "qoder": WaveLimit(default=3, maximum=6),
}
WAVE_BATCH_SET_MAX = max(spec.maximum for spec in WAVE_LIMITS.values())
LENS_REVIEWER_BATCH_MAX = 3


def detect_coordination_host() -> CoordinationHost:
    """Detect the host from its environment; mixed or unknown signals stay on Codex."""
    signals = {
        "cursor": any(os.environ.get(name) for name in CURSOR_ENV_SIGNALS),
        "codex": any(os.environ.get(name) for name in CODEX_ENV_SIGNALS),
        "claude": any(os.environ.get(name) for name in CLAUDE_ENV_SIGNALS),
        "qoder": any(os.environ.get(name) for name in QODER_ENV_SIGNALS),
    }
    detected = [host for host, present in signals.items() if present]
    if len(detected) == 1 and detected[0] in ("cursor", "claude", "qoder"):
        return cast(CoordinationHost, detected[0])
    return "codex"


def resolve_coordination_host(host: str | None) -> CoordinationHost:
    if host in (None, "", "auto"):
        return detect_coordination_host()
    for candidate in COORDINATION_HOSTS:
        if host == candidate:
            return candidate
    raise ValueError("workflow host must be auto, codex, cursor, claude, or qoder")


def resolve_wave_limit(host: CoordinationHost, limit: int | None) -> int:
    spec = WAVE_LIMITS[host]
    resolved = spec.default if limit is None else limit
    if not 1 <= resolved <= spec.maximum:
        raise ValueError(
            f"workflow next limit must be between 1 and {spec.maximum} for host {host}"
        )
    return resolved


def host_models_path() -> Path:
    """Locate the shipped host model defaults in a source checkout or an installed wheel."""
    candidates = (
        Path(__file__).resolve().parents[2] / "profiles" / HOST_MODELS_FILE,
        Path(__file__).resolve().parent / "profiles" / HOST_MODELS_FILE,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Missing host model defaults: {HOST_MODELS_FILE}")


def host_model_defaults(path: Path | None = None) -> dict[str, dict[str, str]]:
    """Recommended per-host role models; projects copy and may override these values."""
    payload = yaml.safe_load((path or host_models_path()).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("host-models.yaml must contain a mapping of host to role models")
    defaults: dict[str, dict[str, str]] = {}
    for host, roles in payload.items():
        if host not in COORDINATION_HOSTS:
            raise ValueError(f"host-models.yaml names an unsupported host: {host}")
        if roles is None:
            roles = {}
        if not isinstance(roles, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in roles.items()
        ):
            raise ValueError(f"host-models.yaml {host} roles must map strings to strings")
        defaults[host] = dict(roles)
    for host in COORDINATION_HOSTS:
        defaults.setdefault(host, {})
    return defaults
