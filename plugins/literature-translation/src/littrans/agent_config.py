"""Optional, conflict-aware project agent installation from portable role resources."""
from __future__ import annotations

import json
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


def configure_agents(project: Path, host: str, workspace: Path | None = None,
                     write: bool = False) -> dict[str, Any]:
    load_project(project)
    root = (workspace or project).resolve()
    project.resolve().relative_to(root)
    if host not in {"codex", "opencode"}:
        raise ValueError("Project agent generation supports codex and opencode")
    resources = resource_root()
    prefix = f".littrans/host-agents/{host}"
    manifest_path = root / f"{prefix}.json"
    previous = read_json(manifest_path).get("files", {}) if manifest_path.exists() else {}
    planned: dict[str, str] = {}
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
        instruction = (f"Read {prefix}/roles/{role.name} relative to the workspace root. "
                       "Follow that role for the assigned LitTrans task packet only. "
                       "References resolve relative to the saved role file. "
                       "Do not assume task completion grants domain approval.")
        if host == "codex":
            body = (f"name = {json.dumps(name)}\ndescription = {json.dumps('LitTrans ' + role.stem)}\n"
                    f"developer_instructions = {json.dumps(instruction)}\n")
            if role.stem in READ_ONLY:
                body += 'sandbox_mode = "read-only"\n'
            agent_path = f".codex/agents/{name}.toml"
        else:
            body = f"---\ndescription: LitTrans {role.stem}\nmode: subagent\n"
            if role.stem in READ_ONLY:
                body += "permission:\n  edit: deny\n  bash: deny\n"
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
              "note": "No model/effort override is generated. Verify native agent discovery in a fresh host session."}
    if not write:
        return report
    if conflicts:
        raise ValueError("User-edited agent resources preserved: " + ", ".join(conflicts))
    with project_write_lock(root):
        # Detect an editor racing the initial check before replacing any files.
        checked = configure_agents(project, host, root, write=False)
        if checked["conflicts"] or checked["changed"] != changed:
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
