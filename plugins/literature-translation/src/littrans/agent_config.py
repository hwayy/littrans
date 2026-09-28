"""Optional, conflict-aware project agent installation from portable role resources."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from littrans.resources import resource_root
from littrans.storage import (
    atomic_write_text,
    load_project,
    project_write_lock,
    read_json,
    restore_files,
    sha256_file,
    sha256_text,
    snapshot_files,
    write_json,
)

READ_ONLY = {"document-scout", "terminology-researcher", "asset-reviewer", "translation-reviewer"}
POLICY_ROLES = {"translator": "translate", "asset-transcriber": "transcribe",
                "translation-reviewer": "audit", "asset-reviewer": "asset-audit",
                "source-reviewer": "source-review"}


def opencode_model(model: str | None, effort: str | None) -> str | None:
    """Convert a project policy to OpenCode 2.x's provider/model#variant selector."""
    if not model:
        if effort:
            raise ValueError("OpenCode reasoning_effort requires a provider/model; unset both to inherit")
        return None
    if not re.fullmatch(r"[^\s/#]+/[^\s#]+(?:#[^\s#]+)?", model):
        raise ValueError("OpenCode model must be provider/model or provider/model#variant")
    base, separator, variant = model.partition("#")
    if effort and not re.fullmatch(r"[^\s#]+", effort):
        raise ValueError("OpenCode reasoning_effort must be a model variant without whitespace or #")
    if separator and effort and variant != effort:
        raise ValueError("OpenCode model variant conflicts with reasoning_effort")
    return f"{base}#{effort or variant}" if effort or variant else base


def configure_agents(project: Path, host: str, workspace: Path | None = None,
                     write: bool = False) -> dict[str, Any]:
    config = load_project(project)
    root = (workspace or project).resolve()
    project.resolve().relative_to(root)
    if host not in {"codex", "opencode"}:
        raise ValueError("Project agent generation supports codex and opencode")
    resources = resource_root()
    prefix = f".littrans/host-agents/{host}"
    manifest_path = root / f"{prefix}.json"
    previous = read_json(manifest_path).get("files", {}) if manifest_path.exists() else {}
    planned: dict[str, str] = {}
    models: dict[str, str | None] = {}
    for folder in ("roles", "references"):
        for path in sorted((resources / folder).glob("*.md")):
            planned[f"{prefix}/{folder}/{path.name}"] = path.read_text(encoding="utf-8")
    if host == "opencode":
        for skill in sorted((resources / "skills").glob("*/SKILL.md")):
            # Keep sibling skill links intact; shared references live in our managed tree.
            planned[f".opencode/skills/{skill.parent.name}/SKILL.md"] = skill.read_text(
                encoding="utf-8").replace("../../references/", f"../../../{prefix}/references/")
    for role in sorted((resources / "roles").glob("*.md")):
        name = "littrans-" + role.stem
        if host == "codex":
            instruction = (
                "Use the assigned task handoff (start.md) as the path anchor. "
                f"Read instructions/roles/{role.name} relative to that handoff's directory; "
                "the task's saved instructions take precedence over installed role copies. "
                "Resolve packet paths against the project root identified by the handoff, "
                "never against the current working directory or a containing Git repository. "
                "If no task handoff was supplied, locate this agent definition at "
                f".codex/agents/{name}.toml and read ../../{prefix}/roles/{role.name} "
                "relative to the definition file's directory. "
                "References resolve relative to the role file actually read. "
                "Follow only the assigned scope; task completion does not grant domain approval. "
                "Do not delegate. Read-only workers return result content for the coordinator to save."
            )
            body = (f"name = {json.dumps(name)}\ndescription = {json.dumps('LitTrans ' + role.stem)}\n"
                    f"developer_instructions = {json.dumps(instruction)}\n")
            if role.stem in READ_ONLY:
                body += 'sandbox_mode = "read-only"\n'
            body += '\n[agents]\nenabled = false\n'
            agent_path = f".codex/agents/{name}.toml"
        else:
            # Resolve from the task/agent file, not the host's possibly different Git root.
            instruction = (
                "Use the assigned task handoff (start.md) as the path anchor. "
                f"Read instructions/roles/{role.name} relative to that handoff's directory; "
                "the task's saved instructions take precedence over installed role copies. "
                "Resolve packet paths against the project root identified by the handoff, "
                "never against the current working directory or a containing Git repository. "
                "If no task handoff was supplied, locate this agent definition at "
                f".opencode/agents/{name}.md and read ../../{prefix}/roles/{role.name} "
                "relative to the definition file's directory. "
                "References resolve relative to the role file actually read. "
                "Follow only the assigned scope; task completion does not grant domain approval."
            )
            policy = config.dispatch(host, POLICY_ROLES.get(role.stem, role.stem))
            model = opencode_model(policy.model, policy.reasoning_effort)
            models[name] = model
            body = f"---\ndescription: LitTrans {role.stem}\nmode: subagent\n"
            if model:
                body += f"model: {json.dumps(model)}\n"
            body += 'permissions:\n  - action: subagent\n    resource: "*"\n    effect: deny\n'
            if role.stem in READ_ONLY:
                for action in ("edit", "shell"):
                    body += f'  - action: {action}\n    resource: "*"\n    effect: deny\n'
            body += f"---\n\n{instruction}\n"
            agent_path = f".opencode/agents/{name}.md"
        planned[agent_path] = body
    conflicts, changed = [], []
    for name, content in planned.items():
        target = root / name
        target.resolve().relative_to(root)
        digest = sha256_file(target) if target.is_file() else None
        if target.exists() and not target.is_file():
            conflicts.append(name)
        elif digest != sha256_text(content):
            changed.append(name)
            if digest is not None and previous.get(name) != digest:
                conflicts.append(name)
    report = {"host": host, "workspace": str(root), "changed": changed,
              "conflicts": conflicts, "written": False,
              "note": ("OpenCode 2.x: project policy is written as provider/model#variant. "
                       "Unset policies inherit the parent model in native child sessions. "
                       "Restart the host after writing; verify actual child models."
                       if host == "opencode" else
                       "No model/effort override is generated. Verify native agent discovery in a fresh host session.")}
    if host == "opencode":
        report["models"] = models
    if not write:
        return report
    if conflicts:
        raise ValueError("User-edited agent resources preserved: " + ", ".join(conflicts))
    with project_write_lock(root):
        # Detect an editor racing the initial check before replacing any files.
        checked = configure_agents(project, host, root, write=False)
        if (checked["conflicts"] or checked["changed"] != changed
                or checked.get("models") != report.get("models")):
            raise ValueError("Agent files changed during preparation; check again")
        snapshots = snapshot_files([root / name for name in planned] + [manifest_path])
        try:
            for name in changed:
                atomic_write_text(root / name, planned[name])
            write_json(manifest_path, {"schema_version": 1, "host": host,
                                      "files": {name: sha256_text(content) for name, content in planned.items()}})
        except BaseException:
            restore_files(snapshots)
            raise
    return {**report, "written": True}
