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

from littrans.build_info import build_identity
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
    repo_root: Path | None = None,
    scaffold: bool = True,
) -> ProjectConfig:
    """Create a schema-6 project and grow its record structure.

    Besides the directories and ``project.yaml``, the project receives the context and
    glossary skeletons, a ``.gitignore`` that keeps the source out and the record in, and
    the handbook, records, ledger, launcher and plugin-facts files under ``repo_root``
    (the project root unless the project is nested in a larger repository).
    """
    from littrans.scaffold import scaffold_project

    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if root.joinpath("project.yaml").exists():
        raise ValueError(f"Project already exists: {root}")
    load_profile(profile)
    initialize_project_dirs(root)
    # A PDF inside the project is recorded relative to it, so a clone on another host
    # finds it under the same name; one kept elsewhere can only be named absolutely.
    project_root = root.resolve()
    recorded_source = source.relative_to(project_root).as_posix() if source.is_relative_to(project_root) else str(source)
    document = fitz.open(source)
    config = ProjectConfig(
        project_id=slugify(title or source.stem),
        title=title or source.stem,
        source_path=recorded_source,
        source_sha256=sha256_file(source),
        source_pages=document.page_count,
        profile=profile,
        source_language=source_language,
        target_language=target_language,
    )
    save_project(root, config)
    if scaffold:
        scaffold_project(root, repo_root=repo_root, refresh=True)
    write_json(
        root / "derived" / "provenance.json",
        {
            "source_path": recorded_source,
            "source_sha256": config.source_sha256,
            "rights_status": config.rights_status,
            "source_is_copied": False,
            "generator": build_identity(),
        },
    )
    return config


def rebuild_project(old: Path, new: Path) -> ProjectConfig:
    """Create a v6 workspace from source/context only, without inheriting approvals."""
    from littrans.scaffold import scaffold_project

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
        copied = ["source"]
        # The decision trace travels with the context it explains; approvals do not.
        for directory in ("context", "glossary", "docs"):
            if (old / directory).is_dir():
                shutil.copytree(old / directory, staging / directory, dirs_exist_ok=True)
                copied.append(directory)
        scaffold_project(staging, refresh=True)
        if payload.get("external_review") is not None:
            from littrans.models import ExternalReviewConfig
            config.external_review = ExternalReviewConfig.model_validate(payload["external_review"])
        save_project(staging, config)
        write_json(staging / "derived" / "provenance.json", {
            "source_path": config.source_path, "source_sha256": config.source_sha256,
            "rights_status": config.rights_status, "source_is_copied": True,
            "generator": build_identity(),
        })
        write_json(staging / "derived" / "rebuild-provenance.json", {
            "historical_project": str(old), "source_sha256": config.source_sha256,
            "copied": copied, "inherited_approvals": False,
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
APPROVED_STATUS = "approved"
REFERENCE_STATUS = "reference-only"
PROPOSED_STATUS = "proposed"
REFERENCE_FILE = "reference.yaml"
DEFAULT_REFERENCE_KIND = "reference"


def _validate_term(path: Path, term: dict[str, Any]) -> None:
    """Refuse an entry whose source forms could never be matched as written."""
    source = str(term.get("source", ""))
    mode = str(term.get("match", "substring"))
    if mode not in TERM_MATCH_MODES:
        raise ValueError(f"{path}: term {source!r} has unknown match mode {mode!r}; use one of {TERM_MATCH_MODES}")
    aliases = term.get("aliases", [])
    if aliases is None:
        aliases = []
    if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
        raise ValueError(f"{path}: term {source!r} must list its aliases as strings")
    if mode == "regex":
        from littrans.evidence import fold_regex_pattern

        for pattern in (source, *aliases):
            try:
                re.compile(fold_regex_pattern(pattern), re.I)
            except re.error as exc:
                raise ValueError(f"{path}: term {pattern!r} is not a valid regular expression: {exc}") from exc


def _read_term_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("terms", []), list):
        raise ValueError(f"{path} must contain a terms list")
    terms = []
    for term in data.get("terms", []):
        if not isinstance(term, dict):
            continue
        _validate_term(path, term)
        terms.append(term)
    return terms


def load_terms(root: Path, filename: str = "approved.yaml", *, enforced_only: bool = True) -> list[dict[str, Any]]:
    """Load glossary entries; by default only those whose ``status`` is enforced.

    An entry without ``status`` counts as ``approved``. ``status: reference-only`` entries
    are never enforced: they travel to packets through ``load_reference_terms``. Any other
    status (``proposed``, ...) is inert unless the caller asks for every entry, e.g. to
    list candidates.
    """
    path = root / "glossary" / filename
    terms = []
    for term in _read_term_file(path):
        if enforced_only and str(term.get("status", APPROVED_STATUS)) != APPROVED_STATUS:
            continue
        terms.append(term)
    return terms


def load_reference_terms(root: Path) -> list[dict[str, Any]]:
    """Load the binding-but-not-gated entries shown to translators and auditors.

    They come from ``approved.yaml`` entries with ``status: reference-only`` and from every
    entry of ``glossary/reference.yaml``, whose ``status`` defaults to ``reference-only``;
    ``proposed`` entries stay inert in both files and ``approved`` is refused in
    ``reference.yaml`` because that file never gates. Each entry carries a ``kind``
    (``reference`` when absent) so proper names, one-word-two-senses registers and other
    project-defined categories stay distinguishable in packets. Every other key is passed
    through verbatim.
    """
    reference: list[dict[str, Any]] = []
    for term in _read_term_file(root / "glossary" / "approved.yaml"):
        if str(term.get("status", APPROVED_STATUS)) == REFERENCE_STATUS:
            reference.append(term)
    path = root / "glossary" / REFERENCE_FILE
    for term in _read_term_file(path):
        status = str(term.get("status", REFERENCE_STATUS))
        if status == PROPOSED_STATUS:
            continue
        if status != REFERENCE_STATUS:
            raise ValueError(
                f"{path}: term {term.get('source', '')!r} has status {status!r}; reference.yaml never gates, "
                f"move a gated entry to approved.yaml"
            )
        reference.append(term)
    return [
        {"kind": str(term.get("kind") or DEFAULT_REFERENCE_KIND), **{k: v for k, v in term.items() if k != "kind"}}
        for term in reference
    ]


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
