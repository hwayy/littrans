"""Transactional configuration editing and the single effective-policy resolver."""
from __future__ import annotations

import copy
import io
from functools import lru_cache
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from littrans.hosts import SUBAGENT_DISPATCH, resolve_coordination_host
from littrans.models import (
    ExternalRecheckConfig,
    ExternalReviewConfig,
    ExternalReviewerConfig,
    ProjectConfig,
)
from littrans.settings import (
    INHERITED_ROLES,
    LENSES,
    ROLES,
    LocalSettings,
    ProjectSettings,
    digest,
    preset,
)
from littrans.storage import atomic_write_text, project_write_lock, sha256_file

COMMANDS = {"claude-code": "claude", "antigravity": "antigravity", "cursor-cli": "agent",
            "codex-cli": "codex", "opencode-cli": "opencode"}


@lru_cache(maxsize=128)
def _yaml_document(text: str) -> dict[str, Any]:
    reader = YAML(typ="rt")
    reader.allow_duplicate_keys = False
    value = reader.load(text)
    if not isinstance(value, dict):
        raise ValueError("Expected a YAML mapping")
    return value


def yaml_read(path: Path) -> dict[str, Any]:
    try:
        # Cache parsing by complete content, never by mtime. Return an isolated tree
        # so edits cannot mutate another read or hide direct file changes.
        return copy.deepcopy(_yaml_document(path.read_text(encoding="utf-8")))
    except Exception as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc


def yaml_text(value: dict[str, Any]) -> str:
    stream = io.StringIO()
    writer = YAML(typ="rt")
    writer.allow_unicode = True
    writer.dump(value, stream)
    return stream.getvalue()


def read_settings(root: Path) -> ProjectSettings:
    return ProjectSettings.model_validate(yaml_read(root / "settings.yaml"))


def read_local(root: Path) -> LocalSettings:
    path = root / "settings.local.yaml"
    return LocalSettings.model_validate(yaml_read(path)) if path.exists() else LocalSettings()


def resolve_command_binding(root: Path, command: str) -> str:
    """A bare executable name stays a PATH lookup; path-like bindings resolve against root."""
    path = Path(command)
    if path.is_absolute():
        return str(path)
    return command if path.parent == Path(".") else str((root / path).resolve())


def bind(config: ProjectConfig, root: Path) -> ProjectConfig:
    settings, local = read_settings(root), read_local(root)
    if set(local.commands) - settings.external_review.reviewers.keys():
        raise ValueError("Local commands refer to unknown reviewer IDs")
    config._settings = settings
    from littrans.models import RoleDispatch
    config.agent_models = {host: {role: RoleDispatch(**policy.resolve(role)) for role in ROLES}
                           for host, policy in settings.agents.items()}
    for key, value in settings.document.model_dump().items():
        setattr(config, key, value)
    config.profile = settings.preset.name
    if local.source_path:
        config.source_path = local.source_path
    source = config.source(root)
    if not source.is_file() or sha256_file(source) != config.source_sha256:
        raise ValueError("Source binding does not match project source identity")
    external = settings.external_review
    if external.primary:
        def reviewer(key: str) -> ExternalReviewerConfig:
            definition = external.reviewers[key]
            command = local.commands.get(key)
            if command is not None:
                command = resolve_command_binding(root, command)
            result = ExternalReviewerConfig(id=key, command=command or COMMANDS[definition.driver],
                                            **definition.model_dump())
            result._timeout_seconds = external.timeout_seconds
            return result
        config.external_review = ExternalReviewConfig(
            enabled=external.enabled, reviewer=reviewer(external.primary),
            fallbacks=[reviewer(key) for key in external.fallbacks],
            recheck=ExternalRecheckConfig.model_validate(external.recheck.model_dump()), domain_expertise=external.domain_expertise)
    else:
        config.external_review = None
    return config


def effective(
    root: Path, host: str = "auto", *, project_config: ProjectConfig | None = None
) -> dict[str, Any]:
    from littrans.storage import load_project

    project_config = project_config or load_project(root)
    settings = project_config.settings
    selected = resolve_coordination_host(host)
    capability = SUBAGENT_DISPATCH[selected]
    policy = settings.agents[selected]
    roles = {role: policy.resolve(role) for role in ROLES}
    lenses = {lens: policy.resolve("audit", lens) for lens in LENSES}
    errors = []
    for role, values in {**roles, **{f"audit_lenses.{k}": v for k, v in lenses.items()}}.items():
        for key, value in values.items():
            supported = capability.project_agent_config or getattr(capability, key)
            if value is not None and not supported:
                target = role if role.startswith("audit_lenses.") else f"roles.{role}"
                errors.append(f"agents.{selected}.{target}.{key} unsupported; use config set PROJECT "
                              f"agents.{selected}.{target}.{key} null --json to inherit host behaviour")
    if selected == "opencode":
        from littrans.agent_config import opencode_model
        for role, values in {**roles, **lenses}.items():
            try:
                opencode_model(values["model"], values["reasoning_effort"])
            except ValueError as exc:
                errors.append(f"agents.opencode {role}: {exc}")
    origins = {}
    for role in ROLES:
        origin = {key: f"agents.{selected}.defaults.{key}" for key in ("model", "reasoning_effort")}
        inherited = INHERITED_ROLES.get(role)
        for item in ([inherited] if inherited else []) + [role]:
            if item in policy.roles:
                origin.update({key: f"agents.{selected}.roles.{item}.{key}"
                               for key in policy.roles[item].model_fields_set})
        origins[role] = origin
    lens_origins = {}
    for lens, override in policy.audit_lenses.items():
        lens_origins[lens] = {**origins["audit"], **{
            key: f"agents.{selected}.audit_lenses.{lens}.{key}" for key in override.model_fields_set}}
    for lens in lenses:
        lens_origins.setdefault(lens, dict(origins["audit"]))
    return {"host": selected, "configured": policy.model_dump(exclude_unset=True),
            "roles": roles, "audit_lenses": lenses, "wave_size": policy.wave_size,
            "supports": {"model": capability.model, "reasoning_effort": capability.reasoning_effort,
                         "project_agent_config": capability.project_agent_config},
            "errors": errors, "origins": origins, "lens_origins": lens_origins, "settings": settings.payload()}


def show(root: Path, local: bool = False) -> dict[str, Any]:
    from littrans.settings import ProjectManifest
    ProjectManifest.model_validate(yaml_read(root / "project.yaml"))
    path = root / ("settings.local.yaml" if local else "settings.yaml")
    payload = yaml_read(path) if path.exists() or not local else LocalSettings().model_dump()
    try:
        normalized = (LocalSettings.model_validate(payload).model_dump() if local
                      else ProjectSettings.model_validate(payload).payload())
    except ValueError:
        # Keep invalid candidates inspectable and repairable, with conflict detection.
        normalized = payload
    return {"value": payload, "sha256": digest(normalized)}


def differences(before: Any, after: Any, prefix: str = "") -> list[dict[str, Any]]:
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            path = f"{prefix}.{key}" if prefix else key
            if key not in before or key not in after:
                result.append({"path": path, "before": before.get(key), "after": after.get(key),
                               "operation": "add" if key not in before else "remove"})
            else:
                result.extend(differences(before[key], after[key], path))
        return result
    return [{"path": prefix, "before": before, "after": after, "operation": "replace"}]


def policy_domains(payload: dict[str, Any]) -> dict[str, str]:
    translation = copy.deepcopy(payload["translation"])
    presentation = {"document_title": payload["document"]["title"],
                    "rights_status": payload["document"]["rights_status"],
                    "equations": translation.pop("equations"),
                    "table_presentation": translation["tables"].pop("presentation"),
                    "table_fallback": translation["tables"].pop("image_fallback_in_final")}
    detect_language = translation["code"].pop("detect_language")
    return {
        "source": digest({"outline_source": payload["outline_source"],
                          "detect_language": detect_language}),
        "translation": digest({"translation": translation, "document": {
            key: payload["document"][key] for key in ("source_language", "target_language")}}),
        "verification": digest(payload["verification"]),
        "presentation": digest(presentation),
        "external": digest({key: value for key, value in payload["external_review"].items()
                            if key not in ("timeout_seconds", "recheck", "fallbacks", "enabled")}),
    }


def impact(changes: list[dict[str, Any]], local: bool) -> dict[str, Any]:
    paths = [item["path"] for item in changes]
    actions = []
    if not local:
        if any(p.startswith(("outline_source", "translation.code.detect_language")) for p in paths):
            actions.append("source extract PROJECT --replace; source verify PROJECT")
        if any(p.startswith(("translation.", "document.source_language", "document.target_language", "verification.")) for p in paths):
            actions.append("translation qa PROJECT BATCH_ID; workflow status PROJECT --batch-ids BATCH_ID")
        if any(p.startswith("external_review.") for p in paths):
            actions.append("translation review external-status PROJECT BATCH_ID")
        if any(p.startswith("agents.opencode") for p in paths):
            actions.append("project agents PROJECT --host opencode --write")
    return {"next_actions": actions, "history_deleted": False}


def task_policy_dependencies(stage: str) -> set[str]:
    """Shared by impact preview and receipt validation."""
    if stage in {"source-review", "scout", "terminology", "transcribe", "asset-audit"}:
        return {"source"}
    domains = {"source", "translation", "verification"}
    if stage == "external-recheck":
        domains.add("external")
    return domains


def apply(root: Path, candidate: dict[str, Any], *, local: bool = False,
          dry_run: bool = False, expect: str | None = None) -> dict[str, Any]:
    with project_write_lock(root):
        before = show(root, local)
        if expect is not None and before["sha256"] != expect:
            raise ValueError("Configuration changed; read the current sha256 and retry")
        if local:
            bindings = LocalSettings.model_validate(candidate)
            settings = read_settings(root)
            after = bindings.model_dump()
        else:
            settings = ProjectSettings.model_validate(candidate)
            bindings = read_local(root)
            after = settings.payload()
        if set(bindings.commands) - settings.external_review.reviewers.keys():
            raise ValueError("Local executable bindings name undefined reviewers; unset them first")
        if local:
            manifest = yaml_read(root / "project.yaml")
            path = Path(bindings.source_path or manifest["source_path"])
            path = path if path.is_absolute() else root / path
            if not path.is_file() or sha256_file(path) != manifest["source_sha256"]:
                raise ValueError("Source binding does not match project identity")
        changes = differences(before["value"], after)
        affected: dict[str, list[str]] = {"tasks": [], "evidence": [], "outputs": []}
        if changes and not local:
            try:
                prior_domains, next_domains = policy_domains(before["value"]), policy_domains(after)
                changed_domains = {key for key in next_domains if next_domains[key] != prior_domains[key]}
            except (KeyError, TypeError):
                changed_domains = {"source", "translation", "verification", "external", "presentation"}
            from littrans.storage import read_json
            for task_path in (root / ".littrans/work/tasks").glob("*/task.json"):
                task = read_json(task_path)
                relevant = task_policy_dependencies(task["stage"])
                if changed_domains & relevant:
                    affected["tasks"].append(task_path.parent.name)
            patterns = []
            if "source" in changed_domains:
                patterns.append("evidence/pages/*.review.json")
            if changed_domains & {"source", "translation", "verification"}:
                patterns.extend(["qa/*.json", "evidence/audits/**/*.json*"])
            if changed_domains & {"source", "translation", "external"}:
                patterns.append("reviews/*.external.json")
            affected["evidence"] = sorted({str(path.relative_to(root)) for pattern in patterns for path in root.glob(pattern)})
            if changed_domains:
                affected["outputs"] = sorted(str(path.relative_to(root)) for path in (root / "output").glob("*")
                                                   if path.is_file() and path.suffix in {".html", ".md", ".json"})
        report = {"sha256": digest(after), "previous_sha256": before["sha256"], "affected": affected,
                  "changes": changes, "valid": True, "written": False, **impact(changes, local)}
        if changes and not dry_run:
            path = root / ("settings.local.yaml" if local else "settings.yaml")
            original = yaml_read(path) if path.exists() else {}
            _merge(original, after)
            atomic_write_text(path, yaml_text(original))
            report["written"] = True
        return report


def _merge(original: dict[str, Any], candidate: dict[str, Any]) -> None:
    for key in list(original):
        if key not in candidate:
            del original[key]
    for key, value in candidate.items():
        if isinstance(value, dict) and isinstance(original.get(key), dict):
            _merge(original[key], value)
        elif original.get(key) != value or key not in original:
            original[key] = value


def edit(root: Path, key: str, value: Any = None, *, unset: bool = False,
         local: bool = False, dry_run: bool = False) -> dict[str, Any]:
    before = show(root, local)
    candidate = copy.deepcopy(before["value"])
    parts = key.split(".")
    current = candidate
    for part in parts[:-1]:
        current = current.setdefault(part, {})
        if not isinstance(current, dict):
            raise ValueError("Configuration key traverses a scalar")
    if unset:
        if parts[-1] not in current:
            raise ValueError("Configuration key does not exist")
        del current[parts[-1]]
    else:
        current[parts[-1]] = value
    return apply(root, candidate, local=local, dry_run=dry_run, expect=before["sha256"])


def reset(root: Path, name: str, dry_run: bool = False) -> dict[str, Any]:
    before = show(root)
    existing = ProjectSettings.model_validate(before["value"])
    candidate = preset(name, **{key: getattr(existing.document, key)
                               for key in ("title", "source_language", "target_language")}).payload()
    candidate["document"] = existing.document.model_dump()
    return apply(root, candidate, dry_run=dry_run, expect=before["sha256"])
