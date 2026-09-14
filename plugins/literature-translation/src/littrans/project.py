from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf as fitz
import yaml
from pydantic import BaseModel

from littrans.models import (
    AuditRun,
    BatchManifest,
    ExternalReviewAttempt,
    ExternalReviewRun,
    PageVerificationReceipt,
    ProjectConfig,
    ProjectStatus,
    ReviewIssue,
    SourceUnit,
    TranslationRecord,
    WorkflowPacketManifest,
)
from littrans.storage import (
    atomic_write_text,
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
        candidate = Path(__file__).resolve().parent / "profiles" / f"{profile}.yaml"
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
    ignore_path = root / ".gitignore"
    existing_ignore = (
        ignore_path.read_text(encoding="utf-8") if ignore_path.is_file() else ""
    )
    if not any(line.strip() == "/.littrans/" for line in existing_ignore.splitlines()):
        separator = "" if not existing_ignore or existing_ignore.endswith("\n") else "\n"
        atomic_write_text(ignore_path, existing_ignore + separator + "/.littrans/\n")
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
    atomic_write_text(
        root / "context" / "document-brief.md",
        "# Document brief\n\nComplete this brief before translating: subject, argument, audience, "
        "terminology, and source style.\n",
    )
    atomic_write_text(
        root / "context" / "style-guide.md",
        "# Translation style\n\n- Translate faithfully into clear Simplified Chinese.\n"
        "- Preserve every {{asset:ID}} reference in its corresponding source block.\n"
        "- Read original formula and table images in context; do not assume candidates verified.\n"
        "- Preserve code indentation, citations, numbers, and protected identifiers.\n"
        "- Keep reader notes separate from translated text.\n",
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


def rebuild_project(old: Path, new: Path) -> ProjectConfig:
    """Create a v6 workspace from source/context only, without inheriting approvals."""
    old, new = old.resolve(), new.resolve()
    if new == old or new.exists():
        raise ValueError("Rebuild requires a new, non-existing directory distinct from OLD")
    payload = yaml.safe_load((old / "project.yaml").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid historical project configuration")
    source = Path(payload["source_path"])
    if not source.is_absolute():
        source = old / source
    if sha256_file(source) != payload["source_sha256"]:
        raise ValueError("Historical source PDF hash changed")
    new.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".littrans-rebuild-", dir=new.parent) as temporary:
        staging = Path(temporary) / "project"
        copied_source = staging / "source" / source.name
        copied_source.parent.mkdir(parents=True)
        shutil.copyfile(source, copied_source)
        if sha256_file(copied_source) != payload["source_sha256"]:
            raise ValueError("Copied source PDF hash changed")
        config = initialize_project(
            copied_source, staging, payload.get("profile", "technical-book"), payload.get("title"),
            payload.get("source_language", "en"), payload.get("target_language", "zh-CN"),
        )
        config.source_path = copied_source.relative_to(staging).as_posix()
        config.rights_status = payload.get("rights_status", config.rights_status)
        for directory in ("context", "glossary"):
            if (old / directory).is_dir():
                shutil.copytree(old / directory, staging / directory, dirs_exist_ok=True)
        if payload.get("external_review") is not None:
            from littrans.models import ExternalReviewConfig
            config.external_review = ExternalReviewConfig.model_validate(payload["external_review"])
        save_project(staging, config)
        write_json(staging / "derived" / "provenance.json", {
            "source_path": config.source_path, "source_sha256": config.source_sha256,
            "rights_status": config.rights_status, "source_is_copied": True,
        })
        write_json(staging / "derived" / "rebuild-provenance.json", {
            "historical_project": str(old), "source_sha256": config.source_sha256,
            "copied": ["source", "context", "glossary"], "inherited_approvals": False,
            "context_policy": "Historical style text is context; the v6 asset-reference contract takes precedence.",
        })
        if new.exists():
            raise ValueError("Rebuild destination appeared during initialization")
        staging.rename(new)

    return config


def translation_map(root: Path) -> dict[str, TranslationRecord]:
    return {
        record.unit_id: record
        for record in read_jsonl(root / "translations" / "current.jsonl", TranslationRecord)
    }


TERM_MATCH_MODES = ("substring", "word", "regex")


def load_terms(root: Path, filename: str = "approved.yaml", *, enforced_only: bool = True) -> list[dict[str, Any]]:
    """Load glossary entries; by default only those whose ``status`` is enforced.

    An entry without ``status`` counts as ``approved``. Any other status (``proposed``,
    ``reference-only``, ...) is inert even inside ``approved.yaml`` unless the caller asks
    for every entry, e.g. to list candidates.
    """
    path = root / "glossary" / filename
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("terms", []), list):
        raise ValueError(f"{path} must contain a terms list")
    terms = []
    for term in data.get("terms", []):
        if not isinstance(term, dict):
            continue
        source = str(term.get("source", ""))
        mode = str(term.get("match", "substring"))
        if mode not in TERM_MATCH_MODES:
            raise ValueError(f"{path}: term {source!r} has unknown match mode {mode!r}; use one of {TERM_MATCH_MODES}")
        if mode == "regex":
            from littrans.evidence import fold_regex_pattern

            try:
                re.compile(fold_regex_pattern(source), re.I)
            except re.error as exc:
                raise ValueError(f"{path}: term {source!r} is not a valid regular expression: {exc}") from exc
        if enforced_only and str(term.get("status", "approved")) != "approved":
            continue
        terms.append(term)
    return terms


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


def schema_models() -> dict[str, type[BaseModel]]:
    from littrans.fidelity_models import FidelityAsset
    from littrans.representation_models import AssetReviewSubmission, AssetSubmission
    return {
        "project.schema.json": ProjectConfig,
        "source-unit.schema.json": SourceUnit,
        "translation-record.schema.json": TranslationRecord,
        "review-issue.schema.json": ReviewIssue,
        "batch-manifest.schema.json": BatchManifest,
        "external-review-run.schema.json": ExternalReviewRun,
        "external-review-attempt.schema.json": ExternalReviewAttempt,
        "page-verification-receipt.schema.json": PageVerificationReceipt,
        "audit-run.schema.json": AuditRun,
        "workflow-packet-manifest.schema.json": WorkflowPacketManifest,
        "fidelity-asset.schema.json": FidelityAsset,
        "asset-submission.schema.json": AssetSubmission,
        "asset-review-submission.schema.json": AssetReviewSubmission,
    }


def schema_mismatches(output: Path) -> list[str]:
    expected = schema_models()
    actual_names = {path.name for path in output.glob("*.json")}
    mismatches = [
        f"missing:{filename}" for filename in sorted(set(expected) - actual_names)
    ]
    mismatches.extend(
        f"unexpected:{filename}" for filename in sorted(actual_names - set(expected))
    )
    for filename, model in expected.items():
        path = output / filename
        if path.is_file() and json.loads(path.read_text(encoding="utf-8")) != model.model_json_schema():
            mismatches.append(f"stale:{filename}")
    return mismatches


def write_schemas(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for filename, model in schema_models().items():
        atomic_write_text(
            output / filename,
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
        )


def promote_status(root: Path, status: ProjectStatus) -> None:
    config = load_project(root)
    config.status = status
    save_project(root, config)
