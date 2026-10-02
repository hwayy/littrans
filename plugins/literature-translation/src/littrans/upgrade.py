"""Explicit, staged v7 -> v8 upgrade. Historical approvals are never inherited."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from littrans.models import ParagraphData, ProjectStatus, SourceUnit, TranslationRecord, utc_now
from littrans.paragraphs import bind_sections, source_binding, structure_errors
from littrans.storage import (
    read_json,
    read_jsonl,
    read_yaml,
    sha256_file,
    staging_directory,
    write_json,
    write_jsonl,
    write_yaml,
)


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def upgrade_project(old: Path, new: Path, *, dry_run: bool = False) -> dict[str, Any]:
    old, new = old.resolve(), new.resolve()
    if new == old or new.is_relative_to(old) or old.is_relative_to(new) or new.exists():
        raise ValueError("Upgrade requires a new, separate, non-existing workspace")
    manifest = read_yaml(old / "project.yaml")
    if manifest.get("schema_version") != 7:
        raise ValueError("project upgrade supports project v7 only; v8 needs no upgrade")
    local = read_yaml(old / "settings.local.yaml") if (old / "settings.local.yaml").exists() else {}
    source = Path(local.get("source_path") or manifest["source_path"])
    if not source.is_absolute():
        source = old / source
    if sha256_file(source) != manifest["source_sha256"]:
        raise ValueError("Historical source PDF hash changed")
    document = read_json(old / "derived/document.json") if (old / "derived/document.json").exists() else {}
    bindings = {path: sha256_file(path) for path in (
        old / "project.yaml", old / "derived/units.jsonl", old / "translations/current.jsonl",
        old / "settings.yaml", old / "settings.local.yaml", old / "derived/document.json",
    ) if path.is_file()}
    units = []
    pending = []
    conversions = []
    for raw in _rows(old / "derived/units.jsonl"):
        if raw.get("schema_version", 3) != 3:
            raise ValueError("Upgrade requires source-unit v3 records")
        legacy_kind = raw["kind"]
        text = raw["source_text"]
        if legacy_kind in {"paragraph", "heading", "list_item"}:
            level = None
            if legacy_kind == "heading":
                levels = {row["level"] for row in document.get("outline", [])
                          if row["page"] == raw["page"] and row["title"].strip() == text.strip()}
                if len(levels) == 1:
                    level = levels.pop()
            raw["kind"] = "paragraph"
            raw["paragraph"] = ParagraphData(
                body_text=text, body_markdown=raw.get("source_markdown"), outline_level=level,
                candidates=[{"kind": "migration", "legacy_kind": legacy_kind,
                             "reason": "Confirm label/body partition, outline and memberships"}],
            ).model_dump(mode="json")
            raw["paragraph_id"] = raw["unit_id"]
            pending.append(raw["unit_id"])
        raw.update(schema_version=4, verification_status="unverified", status="extracted")
        unit = SourceUnit.model_validate(raw)
        unit.source_hash = source_binding(unit)
        units.append(unit)
        conversions.append({"unit_id": unit.unit_id, "old_kind": legacy_kind,
                            "new_kind": unit.kind.value, "requires_source_review": True,
                            "outline_level": unit.paragraph.outline_level if unit.paragraph else None})
    units = bind_sections(units)
    problems = structure_errors(units)
    if problems:
        raise ValueError(f"Historical source relationships need repair before upgrade: {problems}")
    records = []
    for raw in _rows(old / "translations/current.jsonl"):
        if raw.get("schema_version", 2) != 2:
            raise ValueError("Upgrade requires translation-record v2 records")
        raw.update(schema_version=3, status=ProjectStatus.DRAFT.value, label_translations={})
        raw["uncertainties"] = [*raw.get("uncertainties", []),
                               "Confirm the upgraded paragraph body and label translations, then rebind source_hash."]
        records.append(TranslationRecord.model_validate(raw))
    report = {"source_schema_version": 7, "target_schema_version": 8,
              "dry_run": dry_run, "unit_count": len(units), "translation_count": len(records),
              "pending_paragraph_unit_ids": pending,
              "conversions": conversions,
              "next_actions": ["Review upgraded source paragraphs and outline using fresh source packets.",
                               "Create fresh batches; confirm preserved drafts and submit against current source hashes."]}
    if dry_run:
        return report
    new.parent.mkdir(parents=True, exist_ok=True)
    with staging_directory(new.parent, ".littrans-upgrade-") as staging:
        shutil.copytree(old, staging, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git", ".littrans-write-lock", ".venv", "__pycache__"))
        archive = staging / "history/paragraph-upgrade-v7"
        archive.mkdir(parents=True)
        for name in ("evidence", "packets", "batches", "qa", "reviews", "output", "translations", ".littrans", "overrides"):
            path = staging / name
            if path.exists():
                shutil.move(str(path), str(archive / name))
            path.mkdir()
        shutil.copy2(old / "project.yaml", archive / "project.yaml")
        if (old / "derived/units.jsonl").is_file():
            shutil.copy2(old / "derived/units.jsonl", archive / "units.jsonl")
        state = staging / "derived/project-state.json"
        if state.is_file():
            shutil.copy2(state, archive / "project-state.json")
        # Raster evidence remains usable for fresh source review. Approval receipts
        # and task artifacts stay only in the history directory.
        for image in (archive / "evidence").rglob("*"):
            if image.is_file() and image.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                target = staging / "evidence" / image.relative_to(archive / "evidence")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(image, target)
        record_root = (old / (manifest.get("record_root_relative") or ".")).resolve()
        if record_root != old:
            for name in ("docs", "README.md", "AGENTS.md"):
                path = record_root / name
                if path.is_dir():
                    shutil.copytree(path, archive / "record-root" / name)
                elif path.is_file():
                    (archive / "record-root").mkdir(exist_ok=True)
                    shutil.copy2(path, archive / "record-root" / name)
        source_target = staging / "source" / source.name
        source_target.parent.mkdir(parents=True, exist_ok=True)
        if not source_target.exists():
            shutil.copy2(source, source_target)
        if sha256_file(source_target) != manifest["source_sha256"]:
            raise ValueError("Copied source PDF hash changed")
        manifest.update(schema_version=8, source_path=f"source/{source.name}", record_root_relative=".")
        write_yaml(staging / "project.yaml", manifest)
        if local:
            local["source_path"] = None
            write_yaml(staging / "settings.local.yaml", local)
        write_jsonl(staging / "derived/units.jsonl", units)
        write_jsonl(staging / "translations/current.jsonl", records)
        write_json(staging / "derived/project-state.json", {"status": "extracted", "updated_at": utc_now()})
        write_json(staging / "derived/paragraph-upgrade.json", report)
        # Old semantic overrides live in the page ledger too. Retain their raw evidence
        # in history while fresh extraction/review receives no legacy override contract.
        for path in (staging / "derived/fidelity-pages").glob("*.json"):
            payload = read_json(path)
            shutil.copy2(path, archive / path.name)
            payload.pop("source_overrides", None)
            payload.pop("source_overrides_origin", None)
            write_json(path, payload)
        from littrans.storage import load_project
        load_project(staging)
        read_jsonl(staging / "derived/units.jsonl", SourceUnit)
        read_jsonl(staging / "translations/current.jsonl", TranslationRecord)
        if any(sha256_file(path) != digest for path, digest in bindings.items()):
            raise ValueError("Historical workspace changed during upgrade; retry from a stable snapshot")
        if new.exists():
            raise FileExistsError(new)
        staging.rename(new)
    return report
