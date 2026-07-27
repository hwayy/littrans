from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import fitz
import yaml
from pydantic import BaseModel

from littrans.models import ProjectConfig, ProjectStatus, SourceUnit, TranslationRecord
from littrans.storage import (
    initialize_project_dirs,
    load_project,
    plugin_root,
    read_jsonl,
    save_project,
    sha256_file,
    write_json,
    write_yaml,
)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:64] or "translation-project"


def load_profile(profile: str) -> dict[str, Any]:
    candidate = Path(profile)
    if not candidate.is_file():
        candidate = plugin_root() / "profiles" / f"{profile}.yaml"
    if not candidate.is_file():
        raise ValueError(f"Unknown profile: {profile}")
    payload = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Profile must contain a YAML object: {candidate}")
    return payload


def initialize_project(
    source: Path,
    root: Path,
    profile: str,
    title: str | None = None,
    source_language: str = "en",
    target_language: str = "zh-CN",
) -> ProjectConfig:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if root.joinpath("project.yaml").exists():
        raise FileExistsError(f"Project already exists: {root}")
    load_profile(profile)
    initialize_project_dirs(root)
    document = fitz.open(source)
    config = ProjectConfig(
        project_id=slugify(title or source.stem),
        title=title or source.stem,
        source_path=str(source),
        source_sha256=sha256_file(source),
        source_pages=document.page_count,
        profile=profile,
        source_language=source_language,
        target_language=target_language,
    )
    save_project(root, config)
    write_yaml(root / "glossary" / "approved.yaml", {"terms": []})
    write_yaml(root / "glossary" / "candidates.yaml", {"terms": []})
    (root / "context" / "document-brief.md").write_text(
        "# Document brief\n\nComplete this brief before translating: subject, argument, audience, "
        "terminology, and source style.\n",
        encoding="utf-8",
    )
    (root / "context" / "style-guide.md").write_text(
        "# Translation style\n\n- Translate faithfully into clear Simplified Chinese.\n"
        "- Preserve verified inline and display LaTeX exactly.\n"
        "- Translate every structured table cell without changing its shape.\n"
        "- Preserve code indentation, citations, numbers, and protected identifiers.\n"
        "- Keep reader notes separate from translated text.\n",
        encoding="utf-8",
    )
    write_json(
        root / "derived" / "provenance.json",
        {
            "source_path": str(source),
            "source_sha256": config.source_sha256,
            "rights_status": config.rights_status,
            "source_is_copied": False,
        },
    )
    return config


def translation_map(root: Path) -> dict[str, TranslationRecord]:
    return {
        record.unit_id: record
        for record in read_jsonl(root / "translations" / "current.jsonl", TranslationRecord)
    }


def load_terms(root: Path, filename: str = "approved.yaml") -> list[dict[str, Any]]:
    path = root / "glossary" / filename
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("terms", []), list):
        raise ValueError(f"{path} must contain a terms list")
    return [term for term in data.get("terms", []) if isinstance(term, dict)]


def project_status(root: Path) -> dict[str, Any]:
    config = load_project(root)
    units_path = root / "derived" / "units.jsonl"
    translations = translation_map(root)
    batches = sorted(path.name for path in (root / "batches").glob("*") if path.is_dir())
    issues: list[str] = []
    for path in (root / "reviews").glob("*.issues.jsonl"):
        issues.extend(
            line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        )
    unit_count = (
        sum(1 for line in units_path.read_text(encoding="utf-8").splitlines() if line.strip())
        if units_path.exists()
        else 0
    )
    units = read_jsonl(units_path, SourceUnit)
    translatable_ids = {unit.unit_id for unit in units if unit.translatable}
    status_counts = Counter(
        record.status.value
        for unit_id, record in translations.items()
        if unit_id in translatable_ids
    )
    reviewed_count = sum(
        status_counts[status]
        for status in (
            ProjectStatus.MACHINE_REVIEWED.value,
            ProjectStatus.EXTERNAL_REVIEWED.value,
            ProjectStatus.HUMAN_APPROVED.value,
        )
    )
    return {
        "project_id": config.project_id,
        "title": config.title,
        "status": config.status,
        "source_pages": config.source_pages,
        "unit_count": unit_count,
        "translation_count": len(translations),
        "translatable_unit_count": len(translatable_ids),
        "translation_status_counts": dict(sorted(status_counts.items())),
        "machine_reviewed_coverage": (
            reviewed_count / len(translatable_ids) if translatable_ids else 1.0
        ),
        "batches": batches,
        "review_issue_count": len(issues),
        "output_files": sorted(path.name for path in (root / "output").glob("*")),
    }


def write_schemas(output: Path) -> None:
    from littrans.models import (
        BatchManifest,
        ExternalReviewRun,
        ProjectConfig,
        ReviewIssue,
        SourceUnit,
        TranslationRecord,
    )

    output.mkdir(parents=True, exist_ok=True)
    models: dict[str, type[BaseModel]] = {
        "project.schema.json": ProjectConfig,
        "source-unit.schema.json": SourceUnit,
        "translation-record.schema.json": TranslationRecord,
        "review-issue.schema.json": ReviewIssue,
        "batch-manifest.schema.json": BatchManifest,
        "external-review-run.schema.json": ExternalReviewRun,
    }
    for filename, model in models.items():
        (output / filename).write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def promote_status(root: Path, status: ProjectStatus) -> None:
    config = load_project(root)
    config.status = status
    save_project(root, config)
