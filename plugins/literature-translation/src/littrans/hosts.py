"""Coordination-host detection and host-specific wave limits."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

CoordinationHost = Literal["codex", "cursor"]

CURSOR_ENV_SIGNALS = ("CURSOR_TRACE_ID", "CURSOR_AGENT", "CURSOR_INVOKED_AS")
CODEX_ENV_SIGNALS = ("CODEX_THREAD_ID", "CODEX_TASK_ID", "CODEX_CI")


@dataclass(frozen=True, slots=True)
class WaveLimit:
    default: int
    maximum: int


WAVE_LIMITS: dict[CoordinationHost, WaveLimit] = {
    "codex": WaveLimit(default=3, maximum=3),
    "cursor": WaveLimit(default=6, maximum=9),
}
WAVE_BATCH_SET_MAX = max(spec.maximum for spec in WAVE_LIMITS.values())
LENS_REVIEWER_BATCH_MAX = 3


def detect_coordination_host() -> CoordinationHost:
    cursor = any(os.environ.get(name) for name in CURSOR_ENV_SIGNALS)
    codex = any(os.environ.get(name) for name in CODEX_ENV_SIGNALS)
    if cursor and not codex:
        return "cursor"
    return "codex"


def resolve_coordination_host(host: str | None) -> CoordinationHost:
    if host in (None, "", "auto"):
        return detect_coordination_host()
    if host == "codex":
        return "codex"
    if host == "cursor":
        return "cursor"
    raise ValueError("workflow host must be auto, codex, or cursor")


def resolve_wave_limit(host: CoordinationHost, limit: int | None) -> int:
    spec = WAVE_LIMITS[host]
    resolved = spec.default if limit is None else limit
    if not 1 <= resolved <= spec.maximum:
        raise ValueError(
            f"workflow next limit must be between 1 and {spec.maximum} for host {host}"
        )
    return resolved
