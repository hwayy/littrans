"""Validate complete content candidates before publishing a multi-resource change."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from littrans.configuration import yaml_read, yaml_text
from littrans.settings import digest
from littrans.storage import (
    atomic_write_text,
    load_project,
    project_write_lock,
    restore_files,
    snapshot_files,
)

RESOURCES = {"brief": "context/document-brief.md", "style": "context/style-guide.md",
             "approved": "glossary/approved.yaml", "reference": "glossary/reference.yaml",
             "candidates": "glossary/candidates.yaml", "source-structure": "context/source-structure.json"}


def content(root: Path, resource: str) -> str:
    if resource not in RESOURCES:
        raise ValueError("Unknown resource; choose " + ", ".join(RESOURCES))
    return (root / RESOURCES[resource]).read_text(encoding="utf-8")


def semantic(root: Path) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for resource, name in RESOURCES.items():
        path = root / name
        if not path.exists():
            values[resource] = None
        elif path.suffix == ".yaml":
            values[resource] = yaml_read(path)
        elif path.suffix == ".json":
            values[resource] = json.loads(path.read_text(encoding="utf-8"))
        else:
            values[resource] = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return values


def validate(root: Path, resource: str | None = None) -> dict[str, Any]:
    from littrans.glossary import glossary_check
    from littrans.structure_profile import structure_context
    if resource is not None and resource not in RESOURCES:
        raise ValueError("Unknown resource")
    load_project(root)
    values = semantic(root)
    selected = [resource] if resource else list(RESOURCES)
    for key in selected:
        if key in ("brief", "style") and not str(values[key] or "").strip():
            raise ValueError(f"{key} must contain nonempty text")
    glossary = glossary_check(root) if resource in (None, "approved", "reference", "candidates") else None
    if resource in (None, "source-structure") and values["source-structure"] is not None:
        structure_context(root)
    return {"valid": True, "sha256": digest(values), "glossary": glossary}


def apply(root: Path, manifest: Path, dry_run: bool = False, expect: str | None = None) -> dict[str, Any]:
    spec = yaml_read(manifest)
    if set(spec) - {"resources", "reason"} or not isinstance(spec.get("resources"), dict) or not spec["resources"]:
        raise ValueError("Manifest requires a nonempty resources mapping and optional reason")
    unknown = set(spec["resources"]) - RESOURCES.keys()
    if unknown:
        raise ValueError("Unknown context resources: " + ", ".join(sorted(unknown)))
    if "approved" in spec["resources"] and not str(spec.get("reason", "")).strip():
        raise ValueError("Approved terminology changes require a decision reason")
    candidates = {}
    for key, filename in spec["resources"].items():
        path = Path(filename)
        candidates[key] = (path if path.is_absolute() else manifest.parent / path).read_text(encoding="utf-8")
    with project_write_lock(root):
        before = semantic(root)
        if expect is not None and expect != digest(before):
            raise ValueError("Context changed; read the current sha256 and retry")
        config = load_project(root)
        # Staging lives under the project for sandbox portability; source is bound absolutely.
        with tempfile.TemporaryDirectory(prefix="context-candidate-", dir=root) as temporary:
            stage = Path(temporary)
            for folder in ("context", "glossary"):
                if (root / folder).exists():
                    shutil.copytree(root / folder, stage / folder)
            (stage / "derived").mkdir()
            for name in ("units.jsonl", "project-state.json"):
                if (root / "derived" / name).exists():
                    shutil.copy2(root / "derived" / name, stage / "derived" / name)
            for name in ("project.yaml", "settings.yaml"):
                shutil.copy2(root / name, stage / name)
            local = {"schema_version": 1, "source_path": str(config.source(root)), "commands": {}}
            atomic_write_text(stage / "settings.local.yaml", yaml_text(local))
            for key, text in candidates.items():
                atomic_write_text(stage / RESOURCES[key], text)
            validate(stage)
            after = semantic(stage)
        def term_keys(value: dict[str, Any] | None) -> set[tuple[str, str]]:
            return {(item["source"], item.get("scope", "document"))
                    for item in (value or {}).get("terms", [])}
        promoted = (term_keys(after["approved"]) - term_keys(before["approved"])) & term_keys(before["candidates"])
        if promoted and ("candidates" not in candidates or promoted & term_keys(after["candidates"])):
            raise ValueError("Terminology promotion must remove the promoted candidates in the same transaction")
        changed = [key for key in RESOURCES if before[key] != after[key]]
        report = {"valid": True, "written": False, "changed": changed,
                  "previous_sha256": digest(before), "sha256": digest(after),
                  "next_actions": ["source verify PROJECT", "workflow status PROJECT --batch-ids BATCH_ID"] if changed else []}
        if changed and not dry_run:
            decision_path = root / "context/decisions.jsonl"
            saved = snapshot_files([root / RESOURCES[key] for key in changed] + [decision_path])
            try:
                for key in changed:
                    atomic_write_text(root / RESOURCES[key], candidates[key])
                if spec.get("reason"):
                    previous = decision_path.read_text(encoding="utf-8") if decision_path.exists() else ""
                    atomic_write_text(decision_path, previous + json.dumps({"reason": spec["reason"],
                                      "before": digest(before), "after": digest(after)}, ensure_ascii=False) + "\n")
            except BaseException:
                restore_files(saved)
                raise
            report["written"] = True
        return report
