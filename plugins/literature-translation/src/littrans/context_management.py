"""Conservative, inspectable context snapshots without changing audit dependencies."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from littrans.glossary import glossary_check
from littrans.storage import load_project, read_json, sha256_file, write_json

CONTEXT_FILES = ("context/document-brief.md", "context/style-guide.md",
                 "context/source-structure.json", "glossary/approved.yaml",
                 "glossary/reference.yaml", "glossary/candidates.yaml")


def context_snapshot(root: Path) -> dict[str, Any]:
    config = load_project(root)
    return {"schema_version": 1, "source_sha256": config.source_sha256,
            "files": {name: sha256_file(root / name) if (root / name).is_file() else None
                      for name in CONTEXT_FILES}}


def check_context(root: Path) -> dict[str, Any]:
    snapshot = context_snapshot(root)
    missing = [name for name, digest in snapshot["files"].items() if digest is None]
    return {"snapshot": snapshot, "missing": missing, "glossary": glossary_check(root)}


def save_context_snapshot(root: Path, destination: Path) -> dict[str, Any]:
    snapshot = context_snapshot(root)
    destination.resolve().relative_to(root.resolve())
    if destination.exists():
        raise ValueError("Snapshot already exists; use a new name to preserve history")
    write_json(destination, snapshot)
    return {"path": str(destination), **snapshot}


def context_impact(root: Path, previous: Path) -> dict[str, Any]:
    before, current = read_json(previous), context_snapshot(root)
    if before.get("schema_version") != 1 or set(before.get("files", {})) != set(CONTEXT_FILES):
        raise ValueError("Not a version-1 context snapshot")
    if before.get("source_sha256") != current["source_sha256"]:
        raise ValueError("Context snapshot belongs to a different source")
    changed = [name for name in CONTEXT_FILES if before["files"][name] != current["files"][name]]
    effects = []
    if any(name in changed for name in CONTEXT_FILES[:2]):
        effects.append("Global brief/style changes invalidate translation audit coverage.")
    if CONTEXT_FILES[2] in changed:
        effects.append("Structure changes require source verify; page-rule scope determines receipt impact.")
    if any(name in changed for name in CONTEXT_FILES[3:5]):
        effects.append("Terminology changes require per-unit dependency checks in workflow status.")
    if CONTEXT_FILES[5] in changed:
        effects.append("Candidate proposals changed; approval requires a coordinator decision.")
    return {"changed": changed, "effects": effects,
            "note": "Preview only; source verify and workflow status remain authoritative."}
