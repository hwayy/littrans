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
# By host, for the scaffolded launcher, which resolves the installed plugin per client.
HOST_ENV_SIGNALS: dict[CoordinationHost, tuple[str, ...]] = {
    "claude": CLAUDE_ENV_SIGNALS,
    "codex": CODEX_ENV_SIGNALS,
    "cursor": CURSOR_ENV_SIGNALS,
    "qoder": QODER_ENV_SIGNALS,
}

HOST_MODELS_FILE = "host-models.yaml"

# The roles a project may give a dispatch model and a reasoning effort of their own.
# `translate` also covers `revise`, which is translator work.
DISPATCH_ROLES: tuple[str, ...] = ("translate", "transcribe", "audit", "asset-audit")
DISPATCH_FIELDS: tuple[str, ...] = ("model", "reasoning_effort")


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


@dataclass(frozen=True, slots=True)
class SubagentDispatch:
    """What a host's task launcher lets the coordinator choose for a single dispatch."""

    model: bool
    reasoning_effort: bool


# Neither Cursor nor Qoder lets a coordinator choose the model of one dispatched task;
# that host's own policy decides. A configured value is reported, never discarded.
SUBAGENT_DISPATCH: dict[CoordinationHost, SubagentDispatch] = {
    "codex": SubagentDispatch(model=True, reasoning_effort=True),
    # The Agent tool takes `model`; effort comes from the writer agents' frontmatter.
    "claude": SubagentDispatch(model=True, reasoning_effort=False),
    "cursor": SubagentDispatch(model=False, reasoning_effort=False),
    "qoder": SubagentDispatch(model=False, reasoning_effort=False),
}


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


def normalize_role_policy(host: str, roles: object) -> dict[str, dict[str, str]]:
    """Read one host's role policy in either the nested or the legacy flat form.

    Nested is the written form: every role carries its own `model` and
    `reasoning_effort`. The legacy flat form gave a role a bare model string and
    shared one host-level `reasoning_effort`; it is still read, and that shared
    effort becomes the default of every role which does not state its own.
    """
    if roles is None:
        return {}
    if not isinstance(roles, dict):
        raise ValueError(f"agent_models.{host} must map each role to its dispatch policy")
    shared_effort = ""
    normalized: dict[str, dict[str, str]] = {}
    for key, value in roles.items():
        if key == "reasoning_effort":
            if not isinstance(value, str):
                raise ValueError(f"agent_models.{host}.reasoning_effort must be a string")
            shared_effort = value.strip()
            continue
        if key not in DISPATCH_ROLES:
            raise ValueError(
                f"agent_models.{host} names an unsupported role: {key}; supported roles "
                f"are {', '.join(DISPATCH_ROLES)}"
            )
        entry: dict[str, str] = {}
        if isinstance(value, str):
            if value.strip():
                entry["model"] = value.strip()
        elif isinstance(value, dict):
            unsupported = sorted(set(map(str, value)) - set(DISPATCH_FIELDS))
            if unsupported:
                raise ValueError(
                    f"agent_models.{host}.{key} names an unsupported field: "
                    f"{unsupported[0]}; supported fields are {', '.join(DISPATCH_FIELDS)}"
                )
            for field in DISPATCH_FIELDS:
                item = value.get(field)
                if item is None:
                    continue
                if not isinstance(item, str):
                    raise ValueError(f"agent_models.{host}.{key}.{field} must be a string")
                if item.strip():
                    entry[field] = item.strip()
        else:
            raise ValueError(
                f"agent_models.{host}.{key} must be a dispatch mapping or a model string"
            )
        normalized[key] = entry
    if shared_effort:
        for entry in normalized.values():
            entry.setdefault("reasoning_effort", shared_effort)
    return normalized


def normalize_agent_models(policy: object) -> dict[str, dict[str, dict[str, str]]]:
    """Normalize a whole `agent_models` mapping, naming any unsupported key."""
    if policy is None:
        policy = {}
    if not isinstance(policy, dict):
        raise ValueError("agent_models must map a coordination host to its role policy")
    normalized: dict[str, dict[str, dict[str, str]]] = {}
    for host, roles in policy.items():
        if host not in COORDINATION_HOSTS:
            raise ValueError(
                f"agent_models names an unsupported host: {host}; supported hosts are "
                f"{', '.join(COORDINATION_HOSTS)}"
            )
        normalized[str(host)] = normalize_role_policy(str(host), roles)
    for host in COORDINATION_HOSTS:
        normalized.setdefault(host, {})
    return normalized


def dispatch_advisories(
    host: str, role: str, model: str | None, reasoning_effort: str | None
) -> tuple[str, ...]:
    """Report, without blocking, where a role's policy and the host's launcher disagree.

    An unset model or effort is a supported choice: the dispatch then follows the
    host's own default. A value the host cannot take is kept and reported, never
    dropped. Neither case is a reason to refuse a packet.
    """
    capability = SUBAGENT_DISPATCH.get(cast(CoordinationHost, host))
    if capability is None:
        return ()
    notes: list[str] = []
    if model and not capability.model:
        notes.append(
            f"agent_models.{host}.{role}.model is set to {model}, but the plugin cannot "
            f"choose a subagent model per dispatch on {host}; that host's own policy "
            "decides which model runs the task. The packet still records the value."
        )
    elif not model and capability.model:
        notes.append(
            f"agent_models.{host}.{role}.model is not set; the {role} dispatch follows "
            f"{host}'s default subagent model."
        )
    if reasoning_effort and not capability.reasoning_effort:
        detail = (
            " Claude Code applies effort through the `effort` frontmatter of the plugin's "
            "writer agents, not per dispatch."
            if host == "claude"
            else ""
        )
        notes.append(
            f"agent_models.{host}.{role}.reasoning_effort is set to {reasoning_effort}, "
            f"but the plugin cannot choose a reasoning effort per dispatch on {host}."
            f"{detail}"
        )
    elif not reasoning_effort and capability.reasoning_effort:
        notes.append(
            f"agent_models.{host}.{role}.reasoning_effort is not set; the {role} dispatch "
            f"follows {host}'s default reasoning effort."
        )
    return tuple(notes)


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


def host_model_defaults(path: Path | None = None) -> dict[str, dict[str, dict[str, str]]]:
    """Recommended per-host role models; projects copy and may override these values."""
    payload = yaml.safe_load((path or host_models_path()).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("host-models.yaml must contain a mapping of host to role models")
    try:
        return normalize_agent_models(payload)
    except ValueError as exc:
        raise ValueError(f"host-models.yaml: {exc}") from exc
