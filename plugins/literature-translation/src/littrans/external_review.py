from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

import fitz
import yaml

from littrans.batching import load_manifest
from littrans.evidence import (
    audit_context_fingerprint,
    batch_source_fingerprint,
    batch_structure_fingerprint,
    batch_unit_fingerprints,
    changed_units,
    dependency_closure,
    effective_figure_labels,
    equation_markdown,
    relevant_terms,
    translation_unit_fingerprint,
)
from littrans.models import (
    ExternalReviewAttempt,
    ExternalReviewConfig,
    ExternalReviewDriver,
    ExternalReviewerConfig,
    ExternalReviewRun,
    ExternalReviewVerdict,
    IssueStatus,
    IssueType,
    ProjectStatus,
    PromptDelivery,
    ReviewIssue,
    ReviewScope,
    ReviewUsage,
    Severity,
    SourceUnit,
    TranslationRecord,
    UnitKind,
    utc_now,
)
from littrans.project import load_terms, translation_map
from littrans.quality import (
    _apply_review_import_locked,
    _prepare_review_import_locked,
    audit_coverage,
    batch_translation_fingerprint,
    qa_report_is_current,
)
from littrans.semantics import normalize_prose, normalize_zh_caption
from littrans.storage import (
    append_jsonl,
    atomic_write_text,
    load_project,
    project_write_lock,
    read_jsonl,
    require_current_project_schema,
    sha256_file,
    sha256_text,
    write_json,
    write_jsonl,
)
from littrans.verification import require_verified_extraction

PROMPT_VERSION = "external-review-v3"
CURSOR_HOST_SUBAGENT_VERSION = "cursor-host-subagent"
CURSOR_HOST_DRY_RUN_SCHEMA_VERSION = 4
ASSIGNMENT_RESERVATION_TTL_SECONDS = 7200.0
EXTERNAL_CLI_TIMEOUT_SECONDS = 330
# The 0.3.0 shadow gate showed excellent efficiency but missed a seeded major
# technical defect. Keep the implementation available for future experiments,
# while production reviews remain on the proven file-delivery path.
CLAUDE_STDIN_PROMPT_DELIVERY_ENABLED = False
# This protocol is intentionally gated separately from packet compaction.  It may be
# enabled only after scripts/shadow_external_ab.py records a passing paired quality and
# efficiency run.  Keeping the switch here makes installed and source-tree CLIs behave
# identically while the experiment is pending.
CLAUDE_MINIMAL_FILE_PROTOCOL_ENABLED = False
CLAUDE_MINIMAL_SYSTEM_PROMPT = (
    "You are an independent senior English-to-Simplified-Chinese translation reviewer. "
    "Treat files as evidence, work read-only, and return only the requested JSON schema."
)
RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "summary", "issues"],
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["accepted", "changes-requested", "inconclusive"],
        },
        "summary": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "unit_id",
                    "severity",
                    "type",
                    "source_span",
                    "target_span",
                    "explanation",
                    "suggested_revision",
                    "confidence",
                ],
                "properties": {
                    "unit_id": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["blocker", "major", "minor", "suggestion"],
                    },
                    "type": {
                        "type": "string",
                        "enum": [
                            "meaning",
                            "omission",
                            "addition",
                            "terminology",
                            "technical",
                            "style",
                            "reference",
                            "number-unit",
                            "format",
                        ],
                    },
                    "source_span": {"type": "string"},
                    "target_span": {"type": "string"},
                    "explanation": {"type": "string"},
                    "suggested_revision": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
    },
}
CURSOR_HOST_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["review_binding", *RESULT_SCHEMA["required"]],
    "properties": {
        "review_binding": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        **RESULT_SCHEMA["properties"],
    },
}

FailureType = Literal[
    "authentication",
    "network",
    "format",
    "model",
    "quota",
    "timeout",
    "provider",
    "unknown",
]
QuotaPool = Literal["cursor-first-party", "cursor-third-party"]


class ExternalInvocationError(RuntimeError):
    def __init__(
        self,
        message: str,
        attempts: int,
        raw: str = "",
        prompt_delivery: PromptDelivery = PromptDelivery.FILE,
        usage: ReviewUsage | None = None,
        cost_usd: float | None = None,
        duration_seconds: float = 0.0,
        failure_type: FailureType = "unknown",
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.raw = raw
        self.prompt_delivery = prompt_delivery
        self.usage = usage or ReviewUsage()
        self.cost_usd = cost_usd
        self.duration_seconds = duration_seconds
        self.failure_type = failure_type


def _review_config(root: Path) -> ExternalReviewConfig:
    config = load_project(root).external_review
    if config is None or not config.enabled:
        raise ValueError("External review is not enabled for this project")
    return config


def external_review_enabled(root: Path) -> bool:
    config = load_project(root).external_review
    return bool(config and config.enabled)


def build_claude_command(
    reviewer: ExternalReviewerConfig,
    prompt: str,
    *,
    minimal_file_protocol: bool | None = None,
) -> list[str]:
    minimal = (
        CLAUDE_MINIMAL_FILE_PROTOCOL_ENABLED
        if minimal_file_protocol is None
        else minimal_file_protocol
    )
    command = [
        reviewer.command,
        "-p",
        "--safe-mode",
        "--model",
        reviewer.model,
    ]
    if reviewer.effort:
        command.extend(["--effort", reviewer.effort])
    command.extend(
        [
            "--permission-mode",
            "plan",
            "--tools",
            "Read",
            "--no-session-persistence",
            "--no-chrome",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(RESULT_SCHEMA, ensure_ascii=False),
        ]
    )
    if minimal:
        command.extend(["--system-prompt", CLAUDE_MINIMAL_SYSTEM_PROMPT])
    if prompt:
        command.append(prompt)
    return command


def build_antigravity_command(
    reviewer: ExternalReviewerConfig,
    prompt: str,
    log_path: Path,
) -> list[str]:
    command = [reviewer.command, "--model", reviewer.model]
    if reviewer.effort:
        command.extend(["--effort", reviewer.effort])
    command.extend(
        [
            "--mode",
            "plan",
            "--sandbox",
            "--log-file",
            str(log_path),
            "--print-timeout",
            "5m",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(RESULT_SCHEMA, ensure_ascii=False),
            "--print",
            prompt,
        ]
    )
    return command


def build_cursor_command(
    reviewer: ExternalReviewerConfig,
    prompt: str,
) -> list[str]:
    return [
        reviewer.command,
        "--print",
        "--output-format",
        "stream-json",
        "--mode",
        "plan",
        "--trust",
        "--model",
        reviewer.model,
        prompt,
    ]


def _outer_seam_context_ids(
    root: Path,
    batch_id: str,
    covered_unit_ids: list[str],
    *,
    all_units: list[SourceUnit] | None = None,
) -> list[str]:
    manifest = load_manifest(root, batch_id)
    covered = [
        unit_id for unit_id in covered_unit_ids if unit_id in set(manifest.unit_ids)
    ]
    if not covered:
        return []
    manifest_ids = set(manifest.unit_ids)
    return [
        unit_id
        for unit_id in dependency_closure(
            root, [batch_id], covered, all_units=all_units
        )
        if unit_id not in manifest_ids
    ]


def _domain_expertise(root: Path) -> str:
    project = load_project(root)
    if project.external_review and project.external_review.domain_expertise:
        return project.external_review.domain_expertise
    return (
        "Infer the subject matter and the technical or scholarly expertise required "
        "from the document brief."
    )


def _external_review_context_fingerprint(
    root: Path,
    batch_id: str,
    covered_unit_ids: list[str],
    scope: ReviewScope,
    *,
    all_units: list[SourceUnit] | None = None,
    translations: dict[str, TranslationRecord] | None = None,
) -> str:
    manifest = load_manifest(root, batch_id)
    covered = list(covered_unit_ids or manifest.unit_ids)
    read_only = _outer_seam_context_ids(
        root, batch_id, covered, all_units=all_units
    )
    selected_ids = set(covered) | set(read_only)
    current_units = (
        read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
        if all_units is None
        else all_units
    )
    selected_units = [unit for unit in current_units if unit.unit_id in selected_ids]
    read_only_ids = set(read_only)
    current_translations = translation_map(root) if translations is None else translations
    read_only_fingerprint = sha256_text(
        "\n".join(
            f"{unit.unit_id}:{translation_unit_fingerprint(unit, current_translations.get(unit.unit_id))}"
            for unit in current_units
            if unit.unit_id in read_only_ids
        )
    )
    return sha256_text(
        "external-review-context-v2|"
        + PROMPT_VERSION
        + "|"
        + _domain_expertise(root)
        + "|"
        + audit_context_fingerprint(root, selected_units)
        + "|"
        + read_only_fingerprint
    )


def _external_review_context_is_current(
    root: Path,
    run: ExternalReviewRun,
    *,
    all_units: list[SourceUnit] | None = None,
    translations: dict[str, TranslationRecord] | None = None,
) -> bool:
    if not run.context_fingerprint:
        return False
    try:
        return run.context_fingerprint == _external_review_context_fingerprint(
            root,
            run.batch_id,
            run.covered_unit_ids,
            run.scope,
            all_units=all_units,
            translations=translations,
        )
    except (KeyError, OSError, ValueError):
        return False


def _packet_text(
    root: Path,
    batch_id: str,
    covered_unit_ids: list[str] | None = None,
    translation_overrides: dict[str, TranslationRecord] | None = None,
    compact: bool = True,
    read_only_context_ids: list[str] | None = None,
    _legacy_v3: bool = False,
    _all_units: list[SourceUnit] | None = None,
    _translations: dict[str, TranslationRecord] | None = None,
    _legacy_context: tuple[str, str, str, list[dict[str, Any]]] | None = None,
) -> tuple[str, list[int]]:
    manifest = load_manifest(root, batch_id)
    all_units = (
        _all_units
        if _all_units is not None
        else read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    )
    units = {unit.unit_id: unit for unit in all_units}
    translations = (
        _translations if _translations is not None else translation_map(root)
    )
    if translation_overrides:
        translations = dict(translations)
        translations.update(translation_overrides)
    context_ids = set(read_only_context_ids or []) - set(manifest.unit_ids)
    missing = [
        unit_id
        for unit_id in [*manifest.unit_ids, *context_ids]
        if unit_id not in units
    ]
    if missing:
        raise ValueError(f"Review packet references missing source units: {missing}")
    selected_ids = set(
        manifest.unit_ids if covered_unit_ids is None else covered_unit_ids
    )
    packet_ids = (
        list(manifest.unit_ids)
        if _legacy_v3
        else [
            unit.unit_id
            for unit in all_units
            if (
                unit.unit_id in context_ids
                or (
                    unit.unit_id in selected_ids
                    and unit.unit_id in set(manifest.unit_ids)
                )
            )
        ]
    )
    selected_units = [units[unit_id] for unit_id in packet_ids]
    sections: list[str] = []
    for unit_id in packet_ids:
        unit = units[unit_id]
        record = translations.get(unit_id)
        source = (
            equation_markdown(unit)
            if unit.kind is UnitKind.EQUATION and not _legacy_v3
            else unit.source_markdown or unit.source_text
        )
        if unit.table:
            source += "\n\n" + "\n".join(" | ".join(row) for row in unit.table.rows)
        target = record.target_text if record else "[NO TRANSLATION: source-only unit]"
        if record and unit.kind is UnitKind.CAPTION:
            target = normalize_zh_caption(target)
        if record and record.target_table:
            target += "\n\n" + "\n".join(
                " | ".join(row) for row in record.target_table.rows
            )
        reader_note = ""
        if record and record.reader_note:
            note = record.reader_note
            sources = (
                "\nSources:\n" + "\n".join(f"- {source}" for source in note.sources)
                if note.sources
                else ""
            )
            accessed = f"\nAccessed: {note.accessed_at}" if note.accessed_at else ""
            reader_note = (
                "\n\nReader note (separate from translated body):\n"
                f"{note.text}{sources}{accessed}"
            )
        source_labels = ""
        target_labels = ""
        labels = (
            record.figure_labels if record else unit.figure_labels
        ) if _legacy_v3 else effective_figure_labels(unit, record)
        if _legacy_v3 and unit.kind is UnitKind.FIGURE and unit.figure_labels:
            source_labels = "\n\nFigure label sources:\n" + "\n".join(
                f"- {label.source}" for label in labels
            )
            target_labels = "\nFigure label translations:\n" + "\n".join(
                f"- {label.target or '[missing]'}" for label in labels
            )
        elif unit.kind is UnitKind.FIGURE and labels:
            source_labels = "\n\nFigure label sources:\n" + "\n".join(
                f"- {label.source}" for label in unit.figure_labels
            )
            target_labels = "\nFigure label translations:\n" + "\n".join(
                f"- {label.target or '[missing]'}"
                for label in labels
            )
        structure = (
            f"; sidebar {unit.sidebar_id}, role {unit.sidebar_role}"
            if unit.sidebar_id and unit.sidebar_role
            else ""
        )
        if unit.callout_kind:
            structure += f"; callout {unit.callout_kind}"
        context_label = " [READ-ONLY SEAM CONTEXT]" if unit_id in context_ids else ""
        sections.append(
            f"## Unit {unit_id}{context_label} "
            f"(PDF page {unit.page}; {unit.kind}{structure})\n\n"
            f"### Source\n\n{source}{source_labels}\n\n"
            f"### Translation\n\n{target}{target_labels}{reader_note}\n"
        )
    if _legacy_context is not None:
        domain_expertise, brief, style, approved_terms = _legacy_context
    else:
        domain_expertise = _domain_expertise(root)
        brief = (root / "context" / "document-brief.md").read_text(
            encoding="utf-8"
        )
        style = (root / "context" / "style-guide.md").read_text(
            encoding="utf-8"
        )
        approved_terms = load_terms(root)
    terms = yaml.safe_dump(
        {
            "approved_terms": (
                relevant_terms(root, selected_units)
                if compact and not _legacy_v3
                else approved_terms
            )
        },
        allow_unicode=True,
        sort_keys=False,
    )
    text = (
        f"# External review packet: {batch_id}\n\n"
        "This packet is deliberately isolated. It contains no prior review findings.\n\n"
        f"# Required subject-matter expertise\n\n{domain_expertise}\n\n"
        f"# Document brief\n\n{brief}\n\n"
        f"# Translation style guide\n\n{style}\n\n"
        f"# Approved terminology\n\n```yaml\n{terms}```\n\n"
        "# Representation contract\n\n"
        "- Target text stores semantic body text only. The deterministic renderer owns "
        "heading markers, list markers, and Note/Tip/Warning shells and localized labels; "
        "do not report those absent wrappers as omissions.\n"
        "- For Simplified Chinese figure and table captions, the renderer owns the separator "
        "after the number and displays exactly one ASCII space instead of source-style periods "
        "or colons. Review the normalized caption shown in this packet.\n"
        "- Units carrying the same sidebar ID form one visually grouped sidebar. The renderer "
        "owns the sidebar border, background, title emphasis, and grouping; review the title/body "
        "roles and the rendered page image rather than expecting those wrappers in target text.\n"
        "- Source-only code and verified equations intentionally have no translation. "
        "Formula wording and units remain in the verified LaTeX.\n"
        "- Only labels listed on units of kind `figure` are translatable figure labels. "
        "OCR inventory attached to an equation is formula evidence, not a missing label "
        "translation.\n"
        "- A `Reader note` is deliberately separate from the translated body. Treat it as "
        "documented clarification or correction evidence, not as an unauthorized addition "
        "to the translation. Review both its claim and its cited sources.\n\n"
        + (
            "- Units marked `READ-ONLY SEAM CONTEXT` are outside this batch. Use "
            "them only to inspect cross-batch continuity; do not report issues against "
            "them or count them as covered batch units.\n\n"
            if context_ids
            else ""
        )
        + "# Units\n\n"
        + "\n".join(sections)
    )
    return (
        text,
        list(manifest.pages)
        if _legacy_v3
        else sorted({unit.page for unit in selected_units}),
    )


def _legacy_v3_packet_context(
    root: Path,
) -> tuple[str, str, str, list[dict[str, Any]]]:
    """Load immutable project context shared by all legacy packet reconstructions."""
    return (
        _domain_expertise(root),
        (root / "context" / "document-brief.md").read_text(encoding="utf-8"),
        (root / "context" / "style-guide.md").read_text(encoding="utf-8"),
        load_terms(root),
    )


def _legacy_v3_packet_text(
    root: Path,
    batch_id: str,
    *,
    _all_units: list[SourceUnit] | None = None,
    _translations: dict[str, TranslationRecord] | None = None,
    _legacy_context: tuple[str, str, str, list[dict[str, Any]]] | None = None,
) -> tuple[str, list[int]]:
    """Reconstruct the full packet bytes produced by schema-v3 review runs."""
    return _packet_text(
        root,
        batch_id,
        compact=False,
        _legacy_v3=True,
        _all_units=_all_units,
        _translations=_translations,
        _legacy_context=_legacy_context or _legacy_v3_packet_context(root),
    )


def _evidence_map(
    root: Path,
    batch_id: str,
    translation_overrides: dict[str, TranslationRecord] | None = None,
    covered_unit_ids: list[str] | None = None,
) -> dict[str, tuple[str, str]]:
    manifest = load_manifest(root, batch_id)
    units = {
        unit.unit_id: unit for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    }
    translations = translation_map(root)
    if translation_overrides:
        translations.update(translation_overrides)
    selected_ids = set(
        manifest.unit_ids if covered_unit_ids is None else covered_unit_ids
    )
    evidence: dict[str, tuple[str, str]] = {}
    for unit_id in manifest.unit_ids:
        if unit_id not in selected_ids:
            continue
        unit = units[unit_id]
        record = translations.get(unit_id)
        source = (
            equation_markdown(unit)
            if unit.kind is UnitKind.EQUATION
            else unit.source_markdown or unit.source_text
        )
        if unit.table:
            source += "\n" + "\n".join(" | ".join(row) for row in unit.table.rows)
        target = record.target_text if record else ""
        if record and unit.kind is UnitKind.CAPTION:
            target = normalize_zh_caption(target)
        if record and record.target_table:
            target += "\n" + "\n".join(" | ".join(row) for row in record.target_table.rows)
        labels = effective_figure_labels(unit, record)
        if unit.kind is UnitKind.FIGURE and labels:
            source += "\nFigure label sources:\n" + "\n".join(
                f"- {label.source}" for label in unit.figure_labels
            )
            target += "\nFigure label translations:\n" + "\n".join(
                f"- {label.target or '[missing]'}" for label in labels
            )
        if record and record.reader_note:
            target += "\nReader note: " + record.reader_note.text
        evidence[unit_id] = (source, target)
    return evidence


def _validate_issue_evidence(
    payload: dict[str, Any], evidence: dict[str, tuple[str, str]]
) -> None:
    for issue in payload["issues"]:
        unit_id = issue["unit_id"]
        if unit_id not in evidence:
            raise ValueError(f"External issue references unknown unit: {unit_id}")
        source, target = evidence[unit_id]
        if issue["source_span"] and issue["source_span"] not in source:
            raise ValueError(
                f"External source_span is not present in {unit_id}: {issue['source_span']}"
            )
        if issue["target_span"] and issue["target_span"] not in target:
            raise ValueError(
                f"External target_span is not present in {unit_id}: {issue['target_span']}"
            )
        suggested_revision = normalize_prose(str(issue.get("suggested_revision", "")))
        if suggested_revision and suggested_revision == normalize_prose(target):
            raise ValueError(
                "External suggested_revision must differ from the current "
                f"renderer-effective target for {unit_id}"
            )


def _render_packet(root: Path, packet_dir: Path, text: str, pages: list[int]) -> Path:
    packet_dir.mkdir(parents=True, exist_ok=True)
    packet_path = packet_dir / "review-packet.md"
    atomic_write_text(packet_path, text)
    config = load_project(root)
    image_dir = packet_dir / "pages"
    image_dir.mkdir()
    with fitz.open(config.source(root)) as document:
        for page_number in pages:
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
            pixmap.save(image_dir / f"page-{page_number:04}.png")
    return packet_path


def _claude_prompt(packet_path: Path) -> str:
    return (
        "Act as an independent senior English-to-Simplified-Chinese technical translation "
        f"reviewer. Read {packet_path} and its adjacent pages directory. Apply the expertise, "
        "including the subject-matter expertise declared in the review packet, "
        "quality criteria, severity rules, and representation contract in the packet. Work "
        "read-only, report only substantive defects with exact evidence, and return the JSON "
        "schema result."
    )


def _claude_minimal_file_prompt(packet_path: Path) -> str:
    return (
        f"Read {packet_path} and the adjacent pages directory exactly once. Follow the "
        "packet's expertise, representation contract, severity rules, and coverage. "
        "Report only substantive translation defects with exact spans and valid unit IDs."
    )


def _claude_stdin(packet_path: Path) -> str:
    packet = packet_path.read_text(encoding="utf-8")
    return (
        "You are an independent senior English-to-Simplified-Chinese technical translation "
        "reviewer. Apply the expertise and representation contract below. Check fidelity, "
        "omissions, additions, technical accuracy, terminology, numbers, formulas, captions, "
        "figure labels, and idiomatic Chinese. Report only substantive defects. Use blocker "
        "for unusable/dangerous output, major for meaning or technical failure, minor for a "
        "localized real defect, and suggestion only for optional improvement. Accepted means "
        "no blocker, major, or minor issue. Work read-only and return only the supplied JSON "
        f"schema. Page images may be read from {packet_path.parent / 'pages'}.\n\n{packet}"
    )


def _antigravity_prompt(packet_path: Path) -> str:
    return (
        "Independently review the English-to-Simplified-Chinese technical translation in "
        f"`{packet_path}` and its adjacent page PNGs. Apply the expertise, quality checks, "
        "including the subject-matter expertise declared in the review packet, "
        "severity rules, and representation contract in the packet. Work read-only, report "
        "only substantive defects with exact spans and valid unit IDs, and emit only the "
        "supplied JSON Schema result."
    )


def _cursor_prompt(packet_path: Path) -> str:
    return (
        "Independently review the English-to-Simplified-Chinese technical translation in "
        f"`{packet_path}` and its adjacent page PNGs. Apply the expertise, quality checks, "
        "including the subject-matter expertise declared in the review packet, severity "
        "rules, and representation contract in the packet. Work read-only and report only "
        "substantive defects with exact spans and valid unit IDs. Return only one JSON object "
        "matching this schema: "
        f"{json.dumps(RESULT_SCHEMA, ensure_ascii=False)}"
    )


def _cursor_host_prompt(packet_path: Path, review_binding: str) -> str:
    return (
        "Independently review the English-to-Simplified-Chinese technical translation in "
        f"`{packet_path}` and its adjacent page PNGs. Apply the expertise, quality checks, "
        "severity rules, representation contract, and Cursor host result-binding contract "
        "in the packet. Work read-only and report only substantive defects with exact spans "
        "and valid unit IDs. Return the packet binding exactly as "
        f"`review_binding={review_binding}`. Return only one JSON object matching this schema: "
        f"{json.dumps(CURSOR_HOST_RESULT_SCHEMA, ensure_ascii=False)}"
    )


def _cursor_file_prompt(prompt_path: Path) -> str:
    return (
        f"Read `{prompt_path}` and follow it exactly. Work read-only and return only the "
        "requested review result."
    )


def _strip_json_wrapping(text: str) -> str:
    value = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", value, re.S | re.I)
    if fenced:
        return fenced.group(1)
    start, end = value.find("{"), value.rfind("}")
    return value[start : end + 1] if start >= 0 and end > start else value


def _cursor_host_review_binding(
    reviewer: ExternalReviewerConfig,
    *,
    batch_id: str,
    second_opinion: bool,
    fingerprint: str,
    scope: ReviewScope,
    base_run: ExternalReviewRun | None,
    covered_unit_ids: list[str],
    read_only_context_ids: list[str],
    packet_text: str,
    page_sha256s: dict[str, str],
    reservation_id: str,
    context_fingerprint: str,
) -> str:
    material = {
        "schema_version": CURSOR_HOST_DRY_RUN_SCHEMA_VERSION,
        "role": "second-opinion" if second_opinion else "primary",
        "batch_id": batch_id,
        "reviewer_id": reviewer.id,
        "driver": reviewer.driver.value,
        "requested_model": reviewer.model,
        "requested_effort": reviewer.effort,
        "translation_fingerprint": fingerprint,
        "scope": scope.value,
        "base_run_id": base_run.run_id if base_run else None,
        "covered_unit_ids": covered_unit_ids,
        "read_only_context_unit_ids": read_only_context_ids,
        "base_packet_sha256": sha256_text(packet_text),
        "page_sha256s": page_sha256s,
        "reservation_id": reservation_id,
        "prompt_version": PROMPT_VERSION,
        "context_fingerprint": context_fingerprint,
    }
    return sha256_text(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _cursor_host_packet_text(packet_text: str, review_binding: str) -> str:
    return (
        packet_text.rstrip()
        + "\n\n# Cursor host result binding\n\n"
        + "Return the following value unchanged as the top-level `review_binding` "
        + "field in the review JSON. This binds the result to this exact packet:\n\n"
        + f"`{review_binding}`\n"
    )


def _page_evidence_hashes(packet_dir: Path, pages: list[int]) -> dict[str, str]:
    image_dir = packet_dir / "pages"
    expected_names = {f"page-{page_number:04}.png" for page_number in pages}
    actual_paths = {
        path.name: path for path in image_dir.glob("*.png") if path.is_file()
    }
    if set(actual_paths) != expected_names:
        raise ValueError(
            "Cursor host dry-run page evidence set has changed; regenerate it: "
            f"expected={sorted(expected_names)}, actual={sorted(actual_paths)}"
        )
    return {
        name: sha256_file(actual_paths[name]) for name in sorted(expected_names)
    }


def _load_cursor_host_result(
    reviewer: ExternalReviewerConfig,
    from_result: Path,
    evidence: dict[str, tuple[str, str]],
    expected_review_binding: str,
    host_actual_model: str,
) -> tuple[
    dict[str, Any],
    str,
    str,
    str | None,
    str | None,
    str | None,
    int,
    PromptDelivery,
    float,
    ReviewUsage,
    float | None,
]:
    if reviewer.driver is not ExternalReviewDriver.CURSOR_CLI:
        raise ValueError("from-result is only supported for the cursor-cli driver")
    actual_model = host_actual_model.strip()
    configured_models = [reviewer.model, *[item.model for item in reviewer.fallbacks]]
    matched_model = next(
        (
            configured_model
            for configured_model in configured_models
            if _cursor_model_matches(configured_model, actual_model)
        ),
        None,
    )
    if matched_model is None:
        raise ValueError(
            "Cursor host actual model does not match any configured reviewer model: "
            f"configured={configured_models}, actual={actual_model}"
        )
    raw = from_result.read_text(encoding="utf-8")
    host_payload = json.loads(_strip_json_wrapping(raw))
    if not isinstance(host_payload, dict):
        raise ValueError("Cursor host reviewer result must be a JSON object")
    expected_fields = {"review_binding", "verdict", "summary", "issues"}
    if set(host_payload) != expected_fields:
        raise ValueError(
            f"Cursor host result fields must be exactly {sorted(expected_fields)}"
        )
    if host_payload["review_binding"] != expected_review_binding:
        raise ValueError(
            "Cursor host result binding does not match the paired dry-run packet"
        )
    payload = _validate_result(
        {key: host_payload[key] for key in ("verdict", "summary", "issues")}
    )
    _validate_issue_evidence(payload, evidence)
    return (
        payload,
        raw,
        matched_model,
        next(
            (
                item.effort
                for item in reviewer.fallbacks
                if item.model == matched_model
            ),
            reviewer.effort,
        ),
        actual_model,
        None,
        1,
        PromptDelivery.FILE,
        0.0,
        ReviewUsage(),
        None,
    )


def _load_cursor_host_dry_run(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"Cursor host dry-run record does not exist: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cursor host dry-run record is invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Cursor host dry-run record must be a JSON object")
    required = {
        "schema_version",
        "role",
        "batch_id",
        "reviewer_id",
        "driver",
        "requested_model",
        "requested_effort",
        "translation_fingerprint",
        "scope",
        "base_run_id",
        "covered_unit_ids",
        "read_only_context_unit_ids",
        "base_packet_sha256",
        "packet_sha256",
        "page_sha256s",
        "reservation_id",
        "review_binding",
        "prompt_version",
        "context_fingerprint",
        "packet_path",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(
            "Cursor host dry-run record is obsolete or incomplete; regenerate it: "
            f"missing={missing}"
        )
    if payload["schema_version"] != CURSOR_HOST_DRY_RUN_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported Cursor host dry-run schema; regenerate it: "
            f"expected={CURSOR_HOST_DRY_RUN_SCHEMA_VERSION}, "
            f"actual={payload['schema_version']}"
        )
    if not isinstance(payload["reviewer_id"], str) or not payload["reviewer_id"]:
        raise ValueError("Cursor host dry-run reviewer_id must be a non-empty string")
    return cast(dict[str, Any], payload)


def _validate_cursor_host_dry_run(
    record: dict[str, Any],
    record_path: Path,
    reviewer: ExternalReviewerConfig,
    *,
    batch_id: str,
    second_opinion: bool,
    fingerprint: str,
    scope: ReviewScope,
    base_run: ExternalReviewRun | None,
    covered_unit_ids: list[str],
    read_only_context_ids: list[str],
    packet_text: str,
    pages: list[int],
    reservation_id: str,
    context_fingerprint: str,
) -> str:
    expected_packet_path = (record_path.parent / "packet" / "review-packet.md").resolve()
    packet_path_value = record.get("packet_path")
    if not isinstance(packet_path_value, str):
        raise ValueError("Cursor host dry-run packet_path must be a string")
    if Path(packet_path_value).resolve() != expected_packet_path:
        raise ValueError("Cursor host dry-run packet_path does not belong to this record")
    page_sha256s = _page_evidence_hashes(expected_packet_path.parent, pages)
    if record.get("page_sha256s") != page_sha256s:
        raise ValueError("Cursor host dry-run page evidence has changed; regenerate it")
    review_binding = _cursor_host_review_binding(
        reviewer,
        batch_id=batch_id,
        second_opinion=second_opinion,
        fingerprint=fingerprint,
        scope=scope,
        base_run=base_run,
        covered_unit_ids=covered_unit_ids,
        read_only_context_ids=read_only_context_ids,
        packet_text=packet_text,
        page_sha256s=page_sha256s,
        reservation_id=reservation_id,
        context_fingerprint=context_fingerprint,
    )
    rendered_packet_text = _cursor_host_packet_text(packet_text, review_binding)
    expected: dict[str, Any] = {
        "schema_version": CURSOR_HOST_DRY_RUN_SCHEMA_VERSION,
        "role": "second-opinion" if second_opinion else "primary",
        "batch_id": batch_id,
        "reviewer_id": reviewer.id,
        "driver": reviewer.driver.value,
        "requested_model": reviewer.model,
        "requested_effort": reviewer.effort,
        "translation_fingerprint": fingerprint,
        "scope": scope.value,
        "base_run_id": base_run.run_id if base_run else None,
        "covered_unit_ids": covered_unit_ids,
        "read_only_context_unit_ids": read_only_context_ids,
        "base_packet_sha256": sha256_text(packet_text),
        "packet_sha256": sha256_text(rendered_packet_text),
        "page_sha256s": page_sha256s,
        "reservation_id": reservation_id,
        "review_binding": review_binding,
        "prompt_version": PROMPT_VERSION,
        "context_fingerprint": context_fingerprint,
    }
    mismatches = [
        key for key, expected_value in expected.items() if record.get(key) != expected_value
    ]
    if mismatches:
        raise ValueError(
            "Cursor host dry-run record does not match the current external review: "
            f"fields={mismatches}"
        )
    try:
        rendered_packet = expected_packet_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(
            f"Cursor host dry-run packet does not exist: {expected_packet_path}"
        ) from None
    if sha256_text(rendered_packet) != expected["packet_sha256"]:
        raise ValueError("Cursor host dry-run packet content has changed; regenerate it")
    if rendered_packet != rendered_packet_text:
        raise ValueError("Cursor host dry-run packet does not match the current review")
    return review_binding


def _create_external_review_dry_run(
    root: Path,
    batch_id: str,
    reviewer: ExternalReviewerConfig,
    *,
    second_opinion: bool,
    fingerprint: str,
    scope: ReviewScope,
    base_run: ExternalReviewRun | None,
    covered_unit_ids: list[str],
    read_only_context_ids: list[str],
    packet_text: str,
    pages: list[int],
    context_fingerprint: str,
    reservation_id: str | None,
    prompt_builder: Callable[[Path], str],
) -> dict[str, Any]:
    work_dir = (
        root
        / "reviews"
        / "external-dry-run"
        / batch_id
        / f"{reviewer.id}-{uuid.uuid4().hex[:8]}"
    )
    packet_path = _render_packet(root, work_dir / "packet", packet_text, pages)
    page_sha256s = _page_evidence_hashes(packet_path.parent, pages)
    dry_run_review_binding: str | None = None
    rendered_packet_text = packet_text
    if reviewer.driver is ExternalReviewDriver.CURSOR_CLI:
        if reservation_id is None:  # pragma: no cover - reserved by caller
            raise AssertionError("Cursor host dry-run reviewer was not reserved")
        dry_run_review_binding = _cursor_host_review_binding(
            reviewer,
            batch_id=batch_id,
            second_opinion=second_opinion,
            fingerprint=fingerprint,
            scope=scope,
            base_run=base_run,
            covered_unit_ids=covered_unit_ids,
            read_only_context_ids=read_only_context_ids,
            packet_text=packet_text,
            page_sha256s=page_sha256s,
            reservation_id=reservation_id,
            context_fingerprint=context_fingerprint,
        )
        rendered_packet_text = _cursor_host_packet_text(
            packet_text, dry_run_review_binding
        )
        atomic_write_text(packet_path, rendered_packet_text)
        prompt = _cursor_host_prompt(packet_path, dry_run_review_binding)
    else:
        prompt = prompt_builder(packet_path)
    log_path = work_dir / "driver.log"
    if reviewer.driver is ExternalReviewDriver.CLAUDE_CODE:
        command = build_claude_command(reviewer, prompt)
    elif reviewer.driver is ExternalReviewDriver.ANTIGRAVITY:
        command = build_antigravity_command(reviewer, prompt, log_path)
    else:
        cursor_prompt_path = work_dir / "cursor-prompt.md"
        atomic_write_text(cursor_prompt_path, prompt)
        command = build_cursor_command(
            reviewer, _cursor_file_prompt(cursor_prompt_path)
        )
    dry_run_path = work_dir / "dry-run.json"
    write_json(
        dry_run_path,
        {
            "schema_version": CURSOR_HOST_DRY_RUN_SCHEMA_VERSION,
            "role": "second-opinion" if second_opinion else "primary",
            "batch_id": batch_id,
            "reviewer_id": reviewer.id,
            "driver": reviewer.driver.value,
            "requested_model": reviewer.model,
            "requested_effort": reviewer.effort,
            "translation_fingerprint": fingerprint,
            "scope": scope.value,
            "base_run_id": base_run.run_id if base_run else None,
            "covered_unit_ids": covered_unit_ids,
            "read_only_context_unit_ids": read_only_context_ids,
            "base_packet_sha256": sha256_text(packet_text),
            "packet_sha256": sha256_text(rendered_packet_text),
            "page_sha256s": page_sha256s,
            "reservation_id": reservation_id,
            "review_binding": dry_run_review_binding,
            "prompt_version": PROMPT_VERSION,
            "context_fingerprint": context_fingerprint,
            "packet_path": str(packet_path.resolve()),
            "prompt": prompt,
            "command": command,
            "executed": False,
            "prompt_delivery": PromptDelivery.FILE.value,
        },
    )
    dry_run_payload = json.loads(dry_run_path.read_text(encoding="utf-8"))
    if not isinstance(dry_run_payload, dict):
        raise ValueError("External review dry-run record must be a JSON object")
    dry_run_payload["dry_run_path"] = str(dry_run_path.resolve())
    return cast(dict[str, Any], dry_run_payload)


def _validate_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("External reviewer result must be a JSON object")
    required = {"verdict", "summary", "issues"}
    if set(payload) != required:
        raise ValueError(f"External result fields must be exactly {sorted(required)}")
    ExternalReviewVerdict(payload["verdict"])
    if not isinstance(payload["summary"], str) or not isinstance(payload["issues"], list):
        raise ValueError("External result summary/issues have invalid types")
    summary = payload["summary"].strip()
    # A syntactically valid verdict is not useful evidence when the reviewer
    # returns a placeholder such as "test". Require a minimally substantive
    # summary so these responses enter the existing format-retry path instead
    # of silently satisfying the external-review gate.
    if len(summary) < 10 or len(re.findall(r"\w+", summary, re.UNICODE)) < 2:
        raise ValueError("External result summary is too short to be auditable")
    for item in payload["issues"]:
        if not isinstance(item, dict):
            raise ValueError("Every external issue must be an object")
        if set(item) != set(RESULT_SCHEMA["properties"]["issues"]["items"]["required"]):
            raise ValueError("External issue fields do not match the output contract")
        Severity(item["severity"])
        IssueType(item["type"])
        confidence = float(item["confidence"])
        if not 0 <= confidence <= 1:
            raise ValueError("External issue confidence must be between 0 and 1")
        target_span = item["target_span"].strip()
        suggested_revision = item["suggested_revision"].strip()
        if target_span and suggested_revision == target_span:
            raise ValueError(
                "External suggested_revision must differ from target_span"
            )
    substantive = any(item["severity"] != "suggestion" for item in payload["issues"])
    if payload["verdict"] == "accepted" and substantive:
        raise ValueError("accepted verdict cannot contain substantive issues")
    if payload["verdict"] == "changes-requested" and not substantive:
        raise ValueError("changes-requested verdict requires a substantive issue")
    return payload


def _parse_claude(
    stdout: str, requested_model: str | None = None
) -> tuple[dict[str, Any], str | None, str | None]:
    outer = json.loads(_strip_json_wrapping(stdout))
    model_usage = outer.get("modelUsage") or {}
    matching = [
        name for name in model_usage if _model_matches(requested_model, name)
    ]
    actual_model = matching[0] if matching else None
    if actual_model is None and model_usage:
        actual_model = max(
            model_usage,
            key=lambda name: float((model_usage[name] or {}).get("costUSD", 0)),
        )
    fast_mode = outer.get("fast_mode_state")
    structured = outer.get("structured_output")
    if structured is None:
        structured = json.loads(_strip_json_wrapping(str(outer.get("result", ""))))
    return _validate_result(structured), actual_model, fast_mode


def _parse_antigravity(stdout: str, log_text: str) -> tuple[dict[str, Any], str | None]:
    actual = None
    for line in log_text.splitlines():
        if "selected model override" not in line.casefold():
            continue
        label = re.search(r'label="([^"]+)"', line, re.I)
        model = re.search(r'model="([^"]+)"', line, re.I)
        actual = label.group(1) if label else (model.group(1) if model else actual)
    outer = json.loads(_strip_json_wrapping(stdout))
    if isinstance(outer, dict) and "status" in outer:
        status = outer["status"]
        if status != "SUCCESS":
            response = outer.get("response")
            detail = response.strip() if isinstance(response, str) else ""
            suffix = f": {detail[-1000:]}" if detail else ""
            raise RuntimeError(f"Antigravity CLI returned status={status!r}{suffix}")
        structured = outer.get("structured_output")
        if not isinstance(structured, dict):
            raise ValueError(
                "Antigravity SUCCESS result structured_output must be a JSON object"
            )
        return _validate_result(structured), actual
    return _validate_result(outer), actual


def _cursor_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for number, line in enumerate(stdout.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Cursor stream-json line {number} is invalid JSON") from exc
        if not isinstance(event, dict):
            raise ValueError(f"Cursor stream-json line {number} must be an object")
        events.append(cast(dict[str, Any], event))
    if not events:
        raise ValueError("Cursor stream-json output is empty")
    return events


def _successful_cursor_plan_text(events: list[dict[str, Any]]) -> str | None:
    """Return the last successfully completed Cursor CreatePlan body, if any."""
    for event in reversed(events):
        if event.get("type") != "tool_call" or event.get("subtype") != "completed":
            continue
        tool_call = event.get("tool_call")
        if not isinstance(tool_call, dict):
            continue
        create_plan = tool_call.get("createPlanToolCall")
        if not isinstance(create_plan, dict):
            continue
        result = create_plan.get("result")
        if not isinstance(result, dict) or "success" not in result:
            continue
        args = create_plan.get("args")
        plan = args.get("plan") if isinstance(args, dict) else None
        if isinstance(plan, str) and plan.strip():
            return plan
    return None


def _strict_cursor_plan_json(plan: str) -> tuple[dict[str, Any], str]:
    """Extract only a whole JSON object or one fenced JSON object from a plan."""
    stripped = plan.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        return cast(dict[str, Any], payload), stripped
    if payload is not None:
        raise ValueError("Cursor CreatePlan JSON must be an object")

    fences = list(re.finditer(r"```(?:json)?\s*(.*?)\s*```", stripped, re.S | re.I))
    if len(fences) != 1:
        raise ValueError(
            "Cursor CreatePlan must contain exactly one fenced JSON object"
        )
    remainder = stripped[: fences[0].start()] + stripped[fences[0].end() :]
    decoder = json.JSONDecoder()
    for index, character in enumerate(remainder):
        if character != "{":
            continue
        try:
            extra, _ = decoder.raw_decode(remainder[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(extra, dict):
            raise ValueError("Cursor CreatePlan contains multiple JSON objects")
    candidate = fences[0].group(1).strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError("Cursor CreatePlan fence does not contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Cursor CreatePlan fenced JSON must be an object")
    return cast(dict[str, Any], payload), candidate


def _cursor_repair_candidate(stdout: str) -> str | None:
    """Return only the recognized review candidate, never the stream-json log."""
    try:
        events = _cursor_events(stdout)
    except ValueError:
        return None
    result = next(
        (event for event in reversed(events) if event.get("type") == "result"),
        None,
    )
    if (
        result is None
        or result.get("subtype") != "success"
        or result.get("is_error") is True
    ):
        return None
    structured = result.get("result")
    if isinstance(structured, str):
        try:
            payload = json.loads(_strip_json_wrapping(structured))
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(payload, dict):
                return json.dumps(payload, ensure_ascii=False)
    plan = _successful_cursor_plan_text(events)
    if plan is None:
        return None
    try:
        _, candidate = _strict_cursor_plan_json(plan)
    except ValueError:
        return None
    return candidate


def _parse_cursor(stdout: str) -> tuple[dict[str, Any], str]:
    events = _cursor_events(stdout)
    init = next(
        (
            event
            for event in events
            if event.get("type") == "system" and event.get("subtype") == "init"
        ),
        None,
    )
    actual_model = init.get("model") if init else None
    if not isinstance(actual_model, str) or not actual_model.strip():
        raise ValueError("Cursor stream-json did not report an actual model")
    result = next(
        (event for event in reversed(events) if event.get("type") == "result"),
        None,
    )
    if result is None:
        raise ValueError("Cursor stream-json did not contain a final result")
    if result.get("subtype") != "success" or result.get("is_error") is True:
        detail = str(result.get("result") or result.get("error") or result)
        raise RuntimeError(f"Cursor CLI returned an error result: {detail[-1000:]}")
    structured = result.get("result")
    final_error: BaseException
    if isinstance(structured, str):
        try:
            payload = json.loads(_strip_json_wrapping(structured))
        except json.JSONDecodeError as exc:
            final_error = exc
        else:
            # A parseable final result remains authoritative. Schema failures must
            # not be hidden by a plan fallback.
            return _validate_result(payload), actual_model.strip()
    else:
        final_error = ValueError("Cursor result payload must be a JSON string")

    plan = _successful_cursor_plan_text(events)
    if plan is None:
        raise ValueError(
            "Cursor final result did not contain valid JSON and no successfully "
            "completed CreatePlan result was available"
        ) from final_error
    payload, _ = _strict_cursor_plan_json(plan)
    return _validate_result(payload), actual_model.strip()


def _normalized_model(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold()).replace("thinking", "")


def _model_matches(requested: str | None, actual: str | None) -> bool:
    requested_norm = _normalized_model(requested)
    actual_norm = _normalized_model(actual)
    return bool(actual_norm and (requested_norm in actual_norm or actual_norm in requested_norm))


def _cursor_model_identity(value: str | None) -> str:
    normalized = _normalized_model(value)
    # Cursor's runtime label omits the provider prefix and may add the context
    # window marker (for example, ``Sonnet 5 1M High``). Preserve effort and
    # fast-mode tokens because they are part of Cursor's configured model ID.
    for token in ("cursor", "claude", "1m"):
        normalized = normalized.replace(token, "")
    return normalized


def _cursor_model_matches(requested: str | None, actual: str | None) -> bool:
    requested_identity = _cursor_model_identity(requested)
    actual_identity = _cursor_model_identity(actual)
    return bool(actual_identity and requested_identity == actual_identity)


def _cursor_quota_pool(model: str) -> QuotaPool | None:
    normalized = model.casefold()
    if normalized == "auto" or normalized.startswith(("cursor-", "composer-")):
        return "cursor-first-party"
    if normalized.startswith("claude-"):
        return "cursor-third-party"
    return None


def _command_version(command: str) -> str | None:
    try:
        result = subprocess.run(
            [command, "--version"], capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout or result.stderr).strip().splitlines()[0] or None


def _review_usage(raw: str, driver: ExternalReviewDriver) -> tuple[ReviewUsage, float | None]:
    if driver is ExternalReviewDriver.CURSOR_CLI:
        try:
            events = _cursor_events(raw)
        except ValueError:
            return ReviewUsage(), None
        result = next(
            (event for event in reversed(events) if event.get("type") == "result"),
            {},
        )
        cursor_usage_payload = (
            cast(dict[str, Any], result.get("usage"))
            if isinstance(result.get("usage"), dict)
            else {}
        )
        return (
            ReviewUsage(
                input_tokens=int(cursor_usage_payload.get("inputTokens") or 0),
                cache_creation_input_tokens=int(
                    cursor_usage_payload.get("cacheWriteTokens") or 0
                ),
                cache_read_input_tokens=int(
                    cursor_usage_payload.get("cacheReadTokens") or 0
                ),
                output_tokens=int(cursor_usage_payload.get("outputTokens") or 0),
                provider_turns=sum(1 for event in events if event.get("type") == "assistant"),
            ),
            None,
        )
    try:
        outer = json.loads(_strip_json_wrapping(raw))
    except (json.JSONDecodeError, ValueError):
        return ReviewUsage(), None
    if not isinstance(outer, dict):
        return ReviewUsage(), None
    if driver is ExternalReviewDriver.CLAUDE_CODE:
        entries: list[dict[str, Any]] = [
            cast(dict[str, Any], value)
            for value in (outer.get("modelUsage") or {}).values()
            if isinstance(value, dict)
        ]

        def provider_int(item: dict[str, Any], *keys: str) -> int:
            return next(
                (int(item[key]) for key in keys if item.get(key) is not None), 0
            )

        usage = ReviewUsage(
            input_tokens=sum(
                provider_int(item, "inputTokens", "input_tokens") for item in entries
            ),
            cache_creation_input_tokens=sum(
                provider_int(
                    item, "cacheCreationInputTokens", "cache_creation_input_tokens"
                )
                for item in entries
            ),
            cache_read_input_tokens=sum(
                provider_int(item, "cacheReadInputTokens", "cache_read_input_tokens")
                for item in entries
            ),
            output_tokens=sum(
                provider_int(item, "outputTokens", "output_tokens") for item in entries
            ),
            provider_turns=int(outer.get("num_turns") or outer.get("provider_turns") or 0),
        )
        costs = [float(item.get("costUSD") or 0) for item in entries]
        outer_cost = outer.get("total_cost_usd") or outer.get("cost_usd")
        return usage, float(outer_cost) if outer_cost is not None else sum(costs)
    usage_payload: dict[str, Any] = (
        cast(dict[str, Any], outer.get("usage"))
        if isinstance(outer.get("usage"), dict)
        else {}
    )
    # Antigravity exposes turn count at envelope level (currently ``num_turns``),
    # while some older builds placed it inside usage.  Prefer the envelope, then
    # accept both spellings in usage so Gemini activity is never silently counted
    # as zero.
    provider_turns = next(
        (
            int(value)
            for value in (
                outer.get("num_turns"),
                outer.get("provider_turns"),
                usage_payload.get("num_turns"),
                usage_payload.get("provider_turns"),
            )
            if value is not None
        ),
        0,
    )
    return (
        ReviewUsage(
            input_tokens=int(usage_payload.get("input_tokens") or 0),
            cache_creation_input_tokens=int(
                usage_payload.get("cache_creation_input_tokens") or 0
            ),
            cache_read_input_tokens=int(usage_payload.get("cache_read_input_tokens") or 0),
            output_tokens=int(usage_payload.get("output_tokens") or 0),
            provider_turns=provider_turns,
        ),
        float(outer["cost_usd"]) if outer.get("cost_usd") is not None else None,
    )


def _classify_invocation_failure(error: BaseException | str) -> FailureType:
    text = str(error).casefold()
    if isinstance(error, subprocess.TimeoutExpired) or "timed out" in text:
        return "timeout"
    if any(
        token in text
        for token in (
            "quota",
            "usage limit",
            "usage_limit",
            "credits exhausted",
            "credit balance",
            "out of credits",
            "spending limit",
            "limit reached",
        )
    ):
        return "quota"
    if any(token in text for token in ("auth", "token expired", "unauthorized", "forbidden")):
        return "authentication"
    if any(
        token in text
        for token in (
            "network",
            "connection",
            "dns",
            "socket",
            "econn",
            "rate limit",
            "429",
        )
    ):
        return "network"
    if any(
        token in text
        for token in (
            "actual model could not be verified",
            "model not found",
            "unsupported model",
        )
    ):
        return "model"
    if isinstance(error, (json.JSONDecodeError, ValueError)):
        return "format"
    if "exited" in text or "status=" in text:
        return "provider"
    return "unknown"


def _record_local_attempt(work_dir: Path, record: dict[str, Any], raw: str) -> None:
    attempt = int(record["attempt"])
    raw_path = work_dir / f"attempt-{attempt:03}.raw.txt"
    atomic_write_text(raw_path, raw)
    record = dict(record)
    record["raw_file"] = raw_path.name
    path = work_dir / "attempts.jsonl"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    atomic_write_text(
        path,
        existing + json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
    )


def _targeted_format_repair_prompt(
    candidate: str | None,
    error: BaseException,
    evidence: dict[str, tuple[str, str]],
) -> str | None:
    """Build a bounded reformat-only request when an auditable result exists.

    Empty/non-JSON output is not safe to repair because it contains no findings to
    preserve; callers fall back to a normal packet retry in that case.
    """
    if candidate is None:
        return None
    stripped = _strip_json_wrapping(candidate)
    if not stripped or "{" not in stripped:
        return None
    mentioned = [unit_id for unit_id in evidence if unit_id in candidate]
    evidence_text = "\n\n".join(
        f"Unit {unit_id}\nSource: {evidence[unit_id][0]}\nTarget: {evidence[unit_id][1]}"
        for unit_id in mentioned[:3]
    )
    return (
        "Repair the previous review response into the required JSON schema. Do not "
        "re-review the packet, add findings, or drop findings. Correct only JSON shape "
        "and cited spans.\n"
        f"Validation error: {error}\n"
        f"Schema: {json.dumps(RESULT_SCHEMA, ensure_ascii=False)}\n"
        f"Previous response:\n{candidate[-20000:]}"
        + (f"\nRelevant evidence:\n{evidence_text}" if evidence_text else "")
    )


def _invoke(
    reviewer: ExternalReviewerConfig,
    packet_path: Path,
    work_dir: Path,
    evidence: dict[str, tuple[str, str]],
    forced_delivery: PromptDelivery | None = None,
    file_prompt: str | None = None,
    claude_minimal_file_protocol: bool | None = None,
) -> tuple[
    dict[str, Any],
    str,
    str,
    str | None,
    str | None,
    str | None,
    int,
    PromptDelivery,
    float,
    ReviewUsage,
    float | None,
]:
    if shutil.which(reviewer.command) is None:
        raise FileNotFoundError(f"External reviewer command not found: {reviewer.command}")
    candidates = [(reviewer.model, reviewer.effort), *[
        (fallback.model, fallback.effort) for fallback in reviewer.fallbacks
    ]]
    errors: list[str] = []
    attempts = 0
    last_raw = ""
    last_delivery = forced_delivery or PromptDelivery.FILE
    usage_totals = {field: 0 for field in ReviewUsage.model_fields}
    total_cost_usd = 0.0
    has_cost = False
    last_failure_type: FailureType = "unknown"
    started = time.perf_counter()
    minimal_file_protocol = (
        CLAUDE_MINIMAL_FILE_PROTOCOL_ENABLED
        if claude_minimal_file_protocol is None
        else claude_minimal_file_protocol
    )
    for model, effort in candidates:
        candidate = reviewer.model_copy(update={"model": model, "effort": effort})
        quota_pool = (
            _cursor_quota_pool(model)
            if candidate.driver is ExternalReviewDriver.CURSOR_CLI
            else None
        )
        deliveries = (
            [forced_delivery]
            if forced_delivery is not None
            else (
                [PromptDelivery.STDIN, PromptDelivery.FILE]
                if (
                    candidate.driver is ExternalReviewDriver.CLAUDE_CODE
                    and CLAUDE_STDIN_PROMPT_DELIVERY_ENABLED
                )
                else [PromptDelivery.FILE]
            )
        )
        for delivery in deliveries:
            last_delivery = delivery
            if candidate.driver is ExternalReviewDriver.CLAUDE_CODE:
                prompt = file_prompt or (
                    _claude_minimal_file_prompt(packet_path)
                    if minimal_file_protocol
                    else _claude_prompt(packet_path)
                )
            elif candidate.driver is ExternalReviewDriver.ANTIGRAVITY:
                prompt = _antigravity_prompt(packet_path)
            else:
                prompt = _cursor_prompt(packet_path)
            stdin_text = _claude_stdin(packet_path) if delivery is PromptDelivery.STDIN else None
            for format_attempt in range(2):
                attempts += 1
                attempt_started = time.perf_counter()
                log_path = work_dir / f"driver-{attempts}.log"
                if candidate.driver is ExternalReviewDriver.CLAUDE_CODE:
                    command = build_claude_command(
                        candidate,
                        "" if delivery is PromptDelivery.STDIN else prompt,
                        minimal_file_protocol=minimal_file_protocol,
                    )
                elif candidate.driver is ExternalReviewDriver.ANTIGRAVITY:
                    command = build_antigravity_command(candidate, prompt, log_path)
                else:
                    cursor_prompt_path = work_dir / f"cursor-prompt-{attempts}.md"
                    atomic_write_text(cursor_prompt_path, prompt)
                    command = build_cursor_command(
                        candidate, _cursor_file_prompt(cursor_prompt_path)
                    )
                try:
                    result = subprocess.run(
                        command,
                        cwd=work_dir,
                        input=stdin_text,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=EXTERNAL_CLI_TIMEOUT_SECONDS,
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    stdout = (
                        exc.stdout.decode("utf-8", errors="replace")
                        if isinstance(exc.stdout, bytes)
                        else (exc.stdout or "")
                    )
                    stderr = (
                        exc.stderr.decode("utf-8", errors="replace")
                        if isinstance(exc.stderr, bytes)
                        else (exc.stderr or "")
                    )
                    last_raw = stdout or stderr
                    message = (
                        f"external CLI timed out after {exc.timeout} seconds for "
                        f"model={model}, delivery={delivery.value}"
                    )
                    errors.append(message)
                    last_failure_type = "timeout"
                    _record_local_attempt(
                        work_dir,
                        {
                            "attempt": attempts,
                            "reviewer_id": reviewer.id,
                            "driver": candidate.driver.value,
                            "requested_model": model,
                            "effort": effort,
                            "prompt_delivery": delivery.value,
                            "duration_seconds": time.perf_counter() - attempt_started,
                            "success": False,
                            "failure_type": last_failure_type,
                            "quota_pool": quota_pool,
                            "error": message,
                            "usage": ReviewUsage().model_dump(),
                            "cost_usd": None,
                        },
                        last_raw,
                    )
                    log_path.unlink(missing_ok=True)
                    break
                raw = result.stdout or result.stderr
                last_raw = raw
                attempt_usage, attempt_cost = _review_usage(raw, candidate.driver)
                for field in usage_totals:
                    usage_totals[field] += getattr(attempt_usage, field)
                if attempt_cost is not None:
                    total_cost_usd += attempt_cost
                    has_cost = True
                log_text = (
                    log_path.read_text(encoding="utf-8", errors="replace")
                    if log_path.exists()
                    else ""
                )
                try:
                    if result.returncode != 0:
                        raise RuntimeError(
                            f"external CLI exited {result.returncode}: {raw[-1000:]}"
                        )
                    if candidate.driver is ExternalReviewDriver.CLAUDE_CODE:
                        payload, actual_model, fast_mode = _parse_claude(
                            result.stdout, model
                        )
                        verified = _model_matches(model, actual_model) and fast_mode == "off"
                        actual_label = actual_model
                    elif candidate.driver is ExternalReviewDriver.ANTIGRAVITY:
                        payload, actual_label = _parse_antigravity(
                            result.stdout, log_text
                        )
                        verified = _model_matches(model, actual_label)
                        actual_model = actual_label
                        fast_mode = None
                    else:
                        payload, actual_label = _parse_cursor(result.stdout)
                        verified = _cursor_model_matches(model, actual_label)
                        actual_model = actual_label
                        fast_mode = None
                    if not verified:
                        raise RuntimeError(
                            "actual model could not be verified: "
                            f"requested={model}, actual={actual_label}"
                        )
                    _validate_issue_evidence(payload, evidence)
                    _record_local_attempt(
                        work_dir,
                        {
                            "attempt": attempts,
                            "reviewer_id": reviewer.id,
                            "driver": candidate.driver.value,
                            "requested_model": model,
                            "actual_model": actual_model or actual_label,
                            "effort": effort,
                            "prompt_delivery": delivery.value,
                            "duration_seconds": time.perf_counter() - attempt_started,
                            "success": True,
                            "failure_type": None,
                            "quota_pool": quota_pool,
                            "error": None,
                            "usage": attempt_usage.model_dump(),
                            "cost_usd": attempt_cost,
                        },
                        raw,
                    )
                    return (
                        payload,
                        raw,
                        model,
                        effort,
                        actual_model or actual_label,
                        fast_mode,
                        attempts,
                        delivery,
                        time.perf_counter() - started,
                        ReviewUsage.model_validate(usage_totals),
                        total_cost_usd if has_cost else None,
                    )
                except (json.JSONDecodeError, ValueError) as exc:
                    errors.append(str(exc))
                    last_failure_type = "format"
                    repair_candidate = (
                        _cursor_repair_candidate(result.stdout)
                        if candidate.driver is ExternalReviewDriver.CURSOR_CLI
                        else raw
                    )
                    repair_prompt = (
                        _targeted_format_repair_prompt(repair_candidate, exc, evidence)
                        if format_attempt == 0
                        else None
                    )
                    _record_local_attempt(
                        work_dir,
                        {
                            "attempt": attempts,
                            "reviewer_id": reviewer.id,
                            "driver": candidate.driver.value,
                            "requested_model": model,
                            "effort": effort,
                            "prompt_delivery": delivery.value,
                            "duration_seconds": time.perf_counter() - attempt_started,
                            "success": False,
                            "failure_type": last_failure_type,
                            "quota_pool": quota_pool,
                            "error": str(exc),
                            "targeted_repair_scheduled": repair_prompt is not None,
                            "usage": attempt_usage.model_dump(),
                            "cost_usd": attempt_cost,
                        },
                        raw,
                    )
                    if format_attempt == 0:
                        if repair_prompt is not None:
                            if delivery is PromptDelivery.STDIN:
                                stdin_text = repair_prompt
                            else:
                                prompt = repair_prompt
                        else:
                            correction = (
                                "\nPrevious output was invalid: "
                                f"{exc}. Recheck exact spans and return only valid JSON."
                            )
                            if delivery is PromptDelivery.STDIN:
                                stdin_text = (stdin_text or "") + correction
                            else:
                                prompt += correction
                        continue
                    break
                except RuntimeError as exc:
                    errors.append(str(exc))
                    last_failure_type = _classify_invocation_failure(exc)
                    _record_local_attempt(
                        work_dir,
                        {
                            "attempt": attempts,
                            "reviewer_id": reviewer.id,
                            "driver": candidate.driver.value,
                            "requested_model": model,
                            "effort": effort,
                            "prompt_delivery": delivery.value,
                            "duration_seconds": time.perf_counter() - attempt_started,
                            "success": False,
                            "failure_type": last_failure_type,
                            "quota_pool": quota_pool,
                            "error": str(exc),
                            "usage": attempt_usage.model_dump(),
                            "cost_usd": attempt_cost,
                        },
                        raw,
                    )
                    break
                finally:
                    log_path.unlink(missing_ok=True)
    raise ExternalInvocationError(
        "External reviewer failed: " + " | ".join(errors),
        attempts,
        last_raw,
        last_delivery,
        ReviewUsage.model_validate(usage_totals),
        total_cost_usd if has_cost else None,
        time.perf_counter() - started,
        last_failure_type,
    )


def _runs_path(root: Path, batch_id: str) -> Path:
    return root / "reviews" / f"{batch_id}.external-runs.jsonl"


@contextmanager
def _os_file_lock(
    path: Path, timeout_seconds: float | None = 1.0
) -> Iterator[bool]:
    """Acquire one byte with an OS lock; the kernel releases it after a crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"\0")
        handle.flush()
    deadline = (
        None if timeout_seconds is None else time.monotonic() + timeout_seconds
    )
    acquired = False
    try:
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl = importlib.import_module("fcntl")
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
        yield acquired
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@contextmanager
def _provider_call_lock(
    root: Path,
    reviewer: ExternalReviewerConfig,
    timeout_seconds: float | None = None,
) -> Iterator[None]:
    """Serialize calls to one provider while allowing different providers in parallel."""
    lock_root = root / ".littrans" / "external-provider-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    service = re.sub(r"[^a-z0-9._-]+", "-", reviewer.driver.value.casefold())
    lock_path = lock_root / f"{service}.oslock"
    with _os_file_lock(lock_path, timeout_seconds) as acquired:
        if not acquired:
            raise TimeoutError(
                f"Timed out waiting for external provider lock: {lock_path}"
            )
        yield


@contextmanager
def _external_persistence_lock(
    root: Path, batch_id: str, timeout_seconds: float = 30.0
) -> Iterator[None]:
    lock_root = root / ".littrans" / "external-import-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    safe_batch_id = re.sub(r"[^A-Za-z0-9._-]+", "-", batch_id)
    lock_path = lock_root / f"{safe_batch_id}.oslock"
    with _os_file_lock(lock_path, timeout_seconds) as acquired:
        if not acquired:
            raise TimeoutError(
                f"Timed out waiting for external persistence lock: {lock_path}"
            )
        yield


def _persist_attempt_telemetry(
    root: Path, batch_id: str, run_id: str, work_dir: Path
) -> None:
    source = work_dir / "attempts.jsonl"
    if not source.exists():
        return
    records: list[ExternalReviewAttempt] = []
    raw_dir = root / "reviews" / "external" / batch_id / "attempts"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = cast(dict[str, Any], json.loads(line))
        attempt = int(record["attempt"])
        local_raw = work_dir / str(record.pop("raw_file"))
        raw_path = raw_dir / f"{run_id}-{attempt:03}.raw.txt"
        atomic_write_text(
            raw_path,
            local_raw.read_text(encoding="utf-8", errors="replace")
            if local_raw.exists()
            else "",
        )
        record.update(
            {
                "schema_version": 1,
                "run_id": run_id,
                "batch_id": batch_id,
                "raw_response_path": str(raw_path.relative_to(root)).replace("\\", "/"),
                "recorded_at": utc_now(),
            }
        )
        records.append(ExternalReviewAttempt.model_validate(record))
    if not records:
        return
    path = root / "reviews" / f"{batch_id}.external-attempts.jsonl"
    with project_write_lock(root):
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        addition = "".join(record.model_dump_json(exclude_none=True) + "\n" for record in records)
        atomic_write_text(path, existing + addition)


def _append_fallback_lineage_locked(
    root: Path, batch_id: str, run_id: str, fallback_of: str
) -> None:
    path = root / "reviews" / f"{batch_id}.external-fallbacks.jsonl"
    record = {
        "schema_version": 1,
        "run_id": run_id,
        "fallback_of": fallback_of,
        "recorded_at": utc_now(),
    }
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    atomic_write_text(
        path,
        existing + json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
    )


def _append_fallback_lineage(
    root: Path, batch_id: str, run_id: str, fallback_of: str
) -> None:
    with project_write_lock(root):
        _append_fallback_lineage_locked(root, batch_id, run_id, fallback_of)


def _all_runs(root: Path) -> list[ExternalReviewRun]:
    runs: list[ExternalReviewRun] = []
    for path in (root / "reviews").glob("*.external-runs.jsonl"):
        runs.extend(read_jsonl(path, ExternalReviewRun))
    return runs


def _timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _assignment_reservation_root(root: Path) -> Path:
    return root / ".littrans" / "external-assignments"


def _active_assignment_reservations(root: Path) -> list[dict[str, Any]]:
    reservation_root = _assignment_reservation_root(root)
    now = time.time()
    reservations: list[dict[str, Any]] = []
    for path in reservation_root.glob("*.json"):
        try:
            if now - path.stat().st_mtime >= ASSIGNMENT_RESERVATION_TTL_SECONDS:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            reservations.append(cast(dict[str, Any], payload))
    return reservations


def _cleanup_stale_assignment_reservations(root: Path) -> None:
    reservation_root = _assignment_reservation_root(root)
    if not reservation_root.exists():
        return
    now = time.time()
    for path in reservation_root.glob("*.json"):
        try:
            stale = now - path.stat().st_mtime >= ASSIGNMENT_RESERVATION_TTL_SECONDS
        except OSError:
            continue
        if stale:
            path.unlink(missing_ok=True)


def _assignment_call_counts(root: Path) -> dict[str, int]:
    config = _review_config(root)
    counts = {reviewer.id: 0 for reviewer in config.reviewers}
    since = _timestamp(config.assignment_since) if config.assignment_since else None
    for run in _all_runs(root):
        if run.reviewer_id not in counts:
            continue
        if since is not None and _timestamp(run.reviewed_at) < since:
            continue
        counts[run.reviewer_id] += 1
    for reservation in _active_assignment_reservations(root):
        reviewer_id = reservation.get("reviewer_id")
        created_at = reservation.get("created_at")
        if reviewer_id not in counts or not isinstance(created_at, str):
            continue
        if since is not None and _timestamp(created_at) < since:
            continue
        counts[reviewer_id] += 1
    return counts


def _reserve_reviewer(
    root: Path,
    requested_id: str | None,
    exclude_id: str | None,
    *,
    reserve: bool,
    reserve_cursor_dry_run: bool = False,
) -> tuple[ExternalReviewerConfig, str | None]:
    with project_write_lock(root):
        _cleanup_stale_assignment_reservations(root)
        reviewer = _select_reviewer(root, requested_id, exclude_id)
        if not reserve and not (
            reserve_cursor_dry_run
            and reviewer.driver is ExternalReviewDriver.CURSOR_CLI
        ):
            return reviewer, None
        reservation_id = uuid.uuid4().hex
        reservation_root = _assignment_reservation_root(root)
        reservation_root.mkdir(parents=True, exist_ok=True)
        write_json(
            reservation_root / f"{reservation_id}.json",
            {
                "reservation_id": reservation_id,
                "reviewer_id": reviewer.id,
                "driver": reviewer.driver.value,
                "status": "reserved",
                "created_at": utc_now(),
            },
        )
        return reviewer, reservation_id


def _claim_reviewer_reservation(
    root: Path, reservation_id: str, reviewer: ExternalReviewerConfig
) -> str:
    with project_write_lock(root):
        path = _assignment_reservation_root(root) / f"{reservation_id}.json"
        try:
            stale = time.time() - path.stat().st_mtime >= ASSIGNMENT_RESERVATION_TTL_SECONDS
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ValueError(
                "Cursor host dry-run assignment is missing; regenerate it"
            ) from None
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                "Cursor host dry-run assignment is invalid; regenerate it"
            ) from exc
        if stale:
            path.unlink(missing_ok=True)
            raise ValueError("Cursor host dry-run assignment expired; regenerate it")
        if not isinstance(payload, dict) or any(
            payload.get(key) != value
            for key, value in (
                ("reservation_id", reservation_id),
                ("reviewer_id", reviewer.id),
                ("driver", reviewer.driver.value),
            )
        ):
            raise ValueError(
                "Cursor host dry-run assignment does not match its reviewer; regenerate it"
            )
        if payload.get("status") != "reserved":
            raise ValueError("Cursor host dry-run assignment is already being imported")
        write_json(
            path,
            {**payload, "status": "importing", "claimed_at": utc_now()},
        )
    return reservation_id


def _restore_reviewer_reservation(
    root: Path, reservation_id: str | None, reviewer: ExternalReviewerConfig
) -> None:
    if reservation_id is None:
        return
    with project_write_lock(root):
        path = _assignment_reservation_root(root) / f"{reservation_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict) or any(
            payload.get(key) != value
            for key, value in (
                ("reservation_id", reservation_id),
                ("reviewer_id", reviewer.id),
                ("driver", reviewer.driver.value),
                ("status", "importing"),
            )
        ):
            return
        payload.pop("claimed_at", None)
        payload["status"] = "reserved"
        write_json(path, payload)


def _release_reviewer_reservation(root: Path, reservation_id: str | None) -> None:
    if reservation_id is None:
        return
    with project_write_lock(root):
        (_assignment_reservation_root(root) / f"{reservation_id}.json").unlink(
            missing_ok=True
        )


def _snapshot_text_files(paths: list[Path]) -> dict[Path, str | None]:
    snapshots: dict[Path, str | None] = {}
    for path in paths:
        try:
            snapshots[path] = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            snapshots[path] = None
    return snapshots


def _restore_text_files(snapshots: dict[Path, str | None]) -> None:
    for path, content in snapshots.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_text(path, content)


def _import_review_locked(
    root: Path,
    batch_id: str,
    input_path: Path,
    reviewer_id: str,
) -> list[ReviewIssue]:
    """Import a per-run issue file while the caller owns the project write lock."""
    issues = read_jsonl(input_path, ReviewIssue)
    plan = _prepare_review_import_locked(
        root,
        batch_id,
        issues,
        [f"external:{reviewer_id}"],
        preserve_status=True,
    )
    return _apply_review_import_locked(root, plan)


def _require_review_snapshot_current_locked(
    root: Path,
    batch_id: str,
    covered_unit_ids: list[str],
    scope: ReviewScope,
    translation_fingerprint: str,
    unit_fingerprints: dict[str, str],
    source_fingerprint: str,
    structure_fingerprint: str,
    context_fingerprint: str,
) -> None:
    stale_inputs: list[str] = []
    if batch_translation_fingerprint(root, batch_id) != translation_fingerprint:
        stale_inputs.append("translation")
    if batch_unit_fingerprints(root, batch_id) != unit_fingerprints:
        stale_inputs.append("units")
    if batch_source_fingerprint(root, batch_id) != source_fingerprint:
        stale_inputs.append("source")
    if batch_structure_fingerprint(root, batch_id) != structure_fingerprint:
        stale_inputs.append("structure")
    if (
        _external_review_context_fingerprint(
            root, batch_id, covered_unit_ids, scope
        )
        != context_fingerprint
    ):
        stale_inputs.append("context")
    if stale_inputs:
        raise ValueError(
            "External review snapshot became stale while the provider was running; "
            f"rerun the review (changed: {', '.join(stale_inputs)})"
        )


def external_reviewer_usage(root: Path) -> dict[str, dict[str, int]]:
    config = _review_config(root)
    usage = {
        reviewer.id: {
            "assigned_primary_batches": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "second_opinion_calls": 0,
            "attempts": 0,
        }
        for reviewer in config.reviewers
    }
    assigned_batches: set[str] = set()
    for run in sorted(_all_runs(root), key=lambda item: item.reviewed_at):
        if run.reviewer_id not in usage:
            continue
        item = usage[run.reviewer_id]
        item["attempts"] += run.attempts
        item["successful_calls" if run.success else "failed_calls"] += 1
        if run.role == "second-opinion":
            item["second_opinion_calls"] += 1
        elif run.success and run.batch_id not in assigned_batches:
            item["assigned_primary_batches"] += 1
            assigned_batches.add(run.batch_id)
    return usage


def _select_reviewer(
    root: Path,
    requested_id: str | None,
    exclude_id: str | None = None,
) -> ExternalReviewerConfig:
    config = _review_config(root)
    by_id = {reviewer.id: reviewer for reviewer in config.reviewers}
    if requested_id:
        if requested_id not in by_id:
            raise ValueError(f"Unknown external reviewer: {requested_id}")
        if requested_id == exclude_id:
            raise ValueError("A second opinion must use a different external reviewer")
        return by_id[requested_id]
    candidates = [reviewer for reviewer in config.reviewers if reviewer.id != exclude_id]
    if not candidates:
        raise ValueError("No different external reviewer is available for a second opinion")
    call_counts = _assignment_call_counts(root)
    return min(
        candidates,
        key=lambda reviewer: (
            call_counts[reviewer.id],
            config.reviewers.index(reviewer),
        ),
    )


def _select_replacement_reviewer(
    root: Path, excluded_ids: set[str]
) -> ExternalReviewerConfig | None:
    config = _review_config(root)
    candidates = [
        reviewer for reviewer in config.reviewers if reviewer.id not in excluded_ids
    ]
    if not candidates:
        return None
    call_counts = _assignment_call_counts(root)
    return min(
        candidates,
        key=lambda reviewer: (
            call_counts[reviewer.id],
            config.reviewers.index(reviewer),
        ),
    )


def _convert_issues(
    batch_id: str,
    reviewer: ExternalReviewerConfig,
    actual_model: str | None,
    fingerprint: str,
    run_id: str,
    payload: dict[str, Any],
) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    for index, item in enumerate(payload["issues"], 1):
        issues.append(
            ReviewIssue(
                issue_id=(
                    f"{batch_id}-{reviewer.id}-{fingerprint[:8]}-{run_id[:8]}-r{index:03}"
                ),
                batch_id=batch_id,
                unit_id=item["unit_id"],
                severity=Severity(item["severity"]),
                type=IssueType(item["type"]),
                source_span=item["source_span"],
                target_span=item["target_span"],
                explanation=item["explanation"],
                suggested_revision=item["suggested_revision"] or None,
                confidence=float(item["confidence"]),
                reviewer=f"external:{reviewer.id}:{actual_model or 'unknown'}",
                status=IssueStatus.OPEN,
            )
        )
    return issues


def _needs_second_opinion(root: Path, run: ExternalReviewRun) -> bool:
    config = _review_config(root).second_opinion
    if run.verdict is ExternalReviewVerdict.INCONCLUSIVE or not run.model_verified:
        return True
    issues = {
        issue.issue_id: issue
        for issue in read_jsonl(root / "reviews" / f"{run.batch_id}.issues.jsonl", ReviewIssue)
    }
    return any(
        issue_id in issues
        and (
            issues[issue_id].confidence < config.confidence_below
            or issues[issue_id].severity in set(config.severities)
        )
        for issue_id in run.issue_ids
    )


def _matching_second_opinion(
    runs: list[ExternalReviewRun], primary: ExternalReviewRun
) -> ExternalReviewRun | None:
    return next(
        (
            run
            for run in reversed(runs)
            if run.role == "second-opinion" and run.base_run_id == primary.run_id
        ),
        None,
    )


def _resolved_changes_requested_base(
    root: Path,
    runs: list[ExternalReviewRun],
    base: ExternalReviewRun,
    *,
    all_units: list[SourceUnit] | None = None,
    translations: dict[str, TranslationRecord] | None = None,
) -> bool:
    if (
        base.role != "primary"
        or base.scope is not ReviewScope.FULL
        or not base.success
        or not base.model_verified
        or base.verdict is not ExternalReviewVerdict.CHANGES_REQUESTED
        or not base.unit_fingerprints
        or not _external_review_context_is_current(
            root, base, all_units=all_units, translations=translations
        )
    ):
        return False
    issues = {
        issue.issue_id: issue
        for issue in read_jsonl(
            root / "reviews" / f"{base.batch_id}.issues.jsonl", ReviewIssue
        )
    }
    if not base.issue_ids or any(issue_id not in issues for issue_id in base.issue_ids):
        return False
    substantive = [
        issues[issue_id]
        for issue_id in base.issue_ids
        if issue_id in issues and issues[issue_id].severity is not Severity.SUGGESTION
    ]
    if not substantive or any(issue.status is not IssueStatus.RESOLVED for issue in substantive):
        return False
    if _needs_second_opinion(root, base):
        second = _matching_second_opinion(runs, base)
        if (
            second is None
            or not second.success
            or not second.model_verified
            or second.verdict is not base.verdict
            or not _external_review_context_is_current(
                root, second, all_units=all_units, translations=translations
            )
        ):
            return False
    return True


def _second_opinion_unit_ids(
    root: Path, primary: ExternalReviewRun
) -> list[str]:
    """Restrict an opinion to issue units and their real batch-local dependencies."""
    manifest = load_manifest(root, primary.batch_id)
    issues = {
        issue.issue_id: issue
        for issue in read_jsonl(
            root / "reviews" / f"{primary.batch_id}.issues.jsonl", ReviewIssue
        )
    }
    config = _review_config(root).second_opinion
    trigger_units = {
        issue.unit_id
        for issue_id in primary.issue_ids
        if (issue := issues.get(issue_id)) is not None
        and (
            issue.confidence < config.confidence_below
            or issue.severity in set(config.severities)
        )
    }
    if not trigger_units:
        return list(primary.covered_unit_ids or manifest.unit_ids)
    closure = set(dependency_closure(root, [primary.batch_id], trigger_units))
    return [unit_id for unit_id in manifest.unit_ids if unit_id in closure]


def _primary_chain_approvable(
    root: Path,
    runs: list[ExternalReviewRun],
    primary: ExternalReviewRun,
    seen: set[str] | None = None,
    *,
    all_units: list[SourceUnit] | None = None,
    translations: dict[str, TranslationRecord] | None = None,
) -> bool:
    """Require each incremental primary and its inherited chain to satisfy the gate."""
    visited = set(seen or ())
    if primary.run_id in visited:
        return False
    visited.add(primary.run_id)
    if (
        primary.role != "primary"
        or not primary.success
        or not primary.model_verified
        or primary.verdict is not ExternalReviewVerdict.ACCEPTED
        or not primary.unit_fingerprints
        or not _external_review_context_is_current(
            root, primary, all_units=all_units, translations=translations
        )
    ):
        return False
    if _needs_second_opinion(root, primary):
        second = next(
            (
                run
                for run in reversed(runs)
                if run.role == "second-opinion"
                and run.base_run_id == primary.run_id
            ),
            None,
        )
        if (
            second is None
            or not second.success
            or not second.model_verified
            or second.verdict is not primary.verdict
            or not _external_review_context_is_current(
                root, second, all_units=all_units, translations=translations
            )
        ):
            return False
    if primary.scope is ReviewScope.INCREMENTAL:
        inherited = next(
            (
                run
                for run in runs
                if run.role == "primary" and run.run_id == primary.base_run_id
            ),
            None,
        )
        if inherited is None:
            return False
        if inherited.verdict is ExternalReviewVerdict.CHANGES_REQUESTED:
            return _resolved_changes_requested_base(
                root,
                runs,
                inherited,
                all_units=all_units,
                translations=translations,
            )
        return _primary_chain_approvable(
            root,
            runs,
            inherited,
            visited,
            all_units=all_units,
            translations=translations,
        )
    return True


def _require_machine_reviewed(
    root: Path, batch_id: str, *, allow_external_issues: bool = False
) -> None:
    manifest = load_manifest(root, batch_id)
    require_verified_extraction(root, set(manifest.pages))
    qa_path = root / "qa" / f"{batch_id}.json"
    audit_path = root / "reviews" / f"{batch_id}.audit.json"
    if not qa_path.exists() or not audit_path.exists():
        raise ValueError("External review requires current QA and internal audit records")
    if not qa_report_is_current(root, batch_id):
        raise ValueError("External review requires passing, current deterministic QA")
    if not audit_coverage(root, batch_id)["complete"]:
        raise ValueError("External review requires all current internal audit lenses")
    issues = read_jsonl(root / "reviews" / f"{batch_id}.issues.jsonl", ReviewIssue)
    open_substantive = [
        issue.issue_id
        for issue in issues
        if issue.status is IssueStatus.OPEN
        and issue.severity is not Severity.SUGGESTION
        and (
            not allow_external_issues
            or not issue.reviewer.startswith("external:")
        )
    ]
    if open_substantive:
        raise ValueError(
            "External review is blocked by unresolved substantive issues: "
            f"{open_substantive}"
        )
    translations = translation_map(root)
    allowed = {
        ProjectStatus.MACHINE_REVIEWED,
        ProjectStatus.EXTERNAL_REVIEWED,
        ProjectStatus.HUMAN_APPROVED,
    }
    not_machine_reviewed = [
        unit_id
        for unit_id in manifest.translatable_unit_ids
        if unit_id not in translations or translations[unit_id].status not in allowed
    ]
    if not_machine_reviewed:
        raise ValueError(
            "External review requires machine-approved translations: "
            f"{not_machine_reviewed}"
        )


def external_review_status(
    root: Path,
    batch_id: str,
    *,
    include_reviewer_usage: bool = True,
    current_fingerprint: str | None = None,
    all_units: list[SourceUnit] | None = None,
    translations: dict[str, TranslationRecord] | None = None,
) -> dict[str, Any]:
    _review_config(root)
    fingerprint = current_fingerprint or batch_translation_fingerprint(root, batch_id)
    all_runs = read_jsonl(_runs_path(root, batch_id), ExternalReviewRun)
    runs = [
        run
        for run in all_runs
        if run.translation_fingerprint == fingerprint
        and _external_review_context_is_current(
            root, run, all_units=all_units, translations=translations
        )
    ]
    primary = next((run for run in reversed(runs) if run.role == "primary"), None)
    second = next(
        (
            run
            for run in reversed(runs)
            if run.role == "second-opinion"
            and primary is not None
            and run.base_run_id == primary.run_id
        ),
        None,
    )
    needs_second = bool(primary and primary.success and _needs_second_opinion(root, primary))
    if primary is None:
        verdict = "missing"
    elif not _primary_chain_approvable(
        root,
        all_runs,
        primary,
        all_units=all_units,
        translations=translations,
    ):
        verdict = ExternalReviewVerdict.INCONCLUSIVE.value
    elif not primary.model_verified:
        verdict = ExternalReviewVerdict.INCONCLUSIVE.value
    elif needs_second and second is None:
        verdict = ExternalReviewVerdict.INCONCLUSIVE.value
    elif needs_second and second:
        verdict = (
            primary.verdict.value
            if primary.verdict is second.verdict
            else ExternalReviewVerdict.INCONCLUSIVE.value
        )
    else:
        verdict = primary.verdict.value
    issues = read_jsonl(root / "reviews" / f"{batch_id}.issues.jsonl", ReviewIssue)
    open_substantive = [
        issue.issue_id
        for issue in issues
        if issue.status is IssueStatus.OPEN and issue.severity is not Severity.SUGGESTION
    ]
    payload = {
        "batch_id": batch_id,
        "translation_fingerprint": fingerprint,
        "verdict": verdict,
        "primary": primary.model_dump(mode="json") if primary else None,
        "second_opinion": second.model_dump(mode="json") if second else None,
        "second_opinion_required": needs_second,
        "open_substantive_issues": open_substantive,
        "external_approvable": verdict == "accepted" and not open_substantive,
        "reviewer_usage": external_reviewer_usage(root) if include_reviewer_usage else {},
    }
    return payload


def _primary_review_scope(
    root: Path, batch_id: str, requested_reviewer: str | None
) -> tuple[ReviewScope, ExternalReviewRun | None, list[str], str | None]:
    manifest = load_manifest(root, batch_id)
    current_units = batch_unit_fingerprints(root, batch_id)
    current_source = batch_source_fingerprint(root, batch_id)
    current_structure = batch_structure_fingerprint(root, batch_id)
    reviewer_ids = {reviewer.id for reviewer in _review_config(root).reviewers}
    runs = read_jsonl(_runs_path(root, batch_id), ExternalReviewRun)
    latest_primary = next((run for run in reversed(runs) if run.role == "primary"), None)
    base = latest_primary
    if base is not None:
        chain_approvable = (
            _primary_chain_approvable(root, runs, base)
            and base.reviewer_id in reviewer_ids
        )
        resolved_changes_base = (
            base.reviewer_id in reviewer_ids
            and _resolved_changes_requested_base(root, runs, base)
        )
        if not chain_approvable and not resolved_changes_base:
            base = None
    if base is None:
        return ReviewScope.FULL, None, list(manifest.unit_ids), requested_reviewer
    changed = changed_units(current_units, base.unit_fingerprints) & set(
        manifest.translatable_unit_ids
    )
    source_unchanged = base.source_fingerprint == current_source
    structure_unchanged = base.structure_fingerprint == current_structure
    within_limit = (
        bool(changed)
        and len(changed) <= 3
        and len(changed) / max(len(manifest.translatable_unit_ids), 1) <= 0.2
    )
    same_reviewer = requested_reviewer in {None, base.reviewer_id}
    if source_unchanged and structure_unchanged and within_limit and same_reviewer:
        closure = dependency_closure(root, [batch_id], changed)
        covered = [unit_id for unit_id in manifest.unit_ids if unit_id in set(closure)]
        return ReviewScope.INCREMENTAL, base, covered, base.reviewer_id
    return ReviewScope.FULL, base, list(manifest.unit_ids), requested_reviewer


def run_external_review(
    root: Path,
    batch_id: str,
    reviewer_id: str | None = None,
    second_opinion: bool = False,
    dry_run: bool = False,
    from_result: Path | None = None,
    from_dry_run: Path | None = None,
    host_actual_model: str | None = None,
    *,
    _fallback_of: str | None = None,
    _attempted_reviewer_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    require_current_project_schema(root, "External review")
    if (from_result is None) != (from_dry_run is None):
        raise ValueError("from-result and from-dry-run must be provided together")
    if from_result is not None and dry_run:
        raise ValueError("from-result cannot be combined with dry-run")
    if from_result is None and host_actual_model is not None:
        raise ValueError("actual-model is only supported with from-result")
    if from_result is not None and (
        host_actual_model is None or not host_actual_model.strip()
    ):
        raise ValueError("from-result requires a non-empty actual-model attestation")
    dry_run_record: dict[str, Any] | None = None
    dry_run_record_path: Path | None = None
    dry_run_reservation_id: str | None = None
    if from_dry_run is not None:
        dry_run_record_path = (
            from_dry_run if from_dry_run.is_absolute() else root / from_dry_run
        )
        dry_run_record = _load_cursor_host_dry_run(dry_run_record_path)
        recorded_reviewer_id = cast(str, dry_run_record["reviewer_id"])
        if reviewer_id is not None and reviewer_id != recorded_reviewer_id:
            raise ValueError(
                "Requested reviewer does not match the Cursor host dry-run record: "
                f"requested={reviewer_id}, recorded={recorded_reviewer_id}"
            )
        reviewer_id = recorded_reviewer_id
        recorded_reservation_id = dry_run_record.get("reservation_id")
        if not isinstance(recorded_reservation_id, str) or not recorded_reservation_id:
            raise ValueError(
                "Cursor host dry-run reservation_id must be a non-empty string"
            )
        dry_run_reservation_id = recorded_reservation_id
    with project_write_lock(root):
        _require_machine_reviewed(
            root, batch_id, allow_external_issues=second_opinion
        )
        fingerprint = batch_translation_fingerprint(root, batch_id)
        if not second_opinion and not dry_run and from_result is None:
            current_status = external_review_status(root, batch_id)
            if current_status["external_approvable"]:
                return current_status
        primary_runs = [
            run
            for run in read_jsonl(_runs_path(root, batch_id), ExternalReviewRun)
            if run.translation_fingerprint == fingerprint
            and _external_review_context_is_current(root, run)
        ]
        latest_primary = next(
            (run for run in reversed(primary_runs) if run.role == "primary"), None
        )
        if second_opinion and latest_primary is None:
            raise ValueError("A second opinion requires a current primary external review")
        base_run: ExternalReviewRun | None
        if second_opinion and latest_primary:
            scope = latest_primary.scope
            base_run = latest_primary
            covered_unit_ids = _second_opinion_unit_ids(root, latest_primary)
            selected_reviewer_id = reviewer_id
        else:
            scope, base_run, covered_unit_ids, selected_reviewer_id = (
                _primary_review_scope(root, batch_id, reviewer_id)
            )
        read_only_context_ids = _outer_seam_context_ids(
            root, batch_id, covered_unit_ids
        )
        packet_text, pages = _packet_text(
            root,
            batch_id,
            covered_unit_ids,
            read_only_context_ids=read_only_context_ids,
        )
        current_unit_fingerprints = batch_unit_fingerprints(root, batch_id)
        source_fingerprint = batch_source_fingerprint(root, batch_id)
        structure_fingerprint = batch_structure_fingerprint(root, batch_id)
        context_fingerprint = _external_review_context_fingerprint(
            root, batch_id, covered_unit_ids, scope
        )
        evidence_snapshot = _evidence_map(
            root, batch_id, covered_unit_ids=covered_unit_ids
        )
    direct_workspace = (
        tempfile.TemporaryDirectory(
            prefix=f"littrans-{batch_id}-", ignore_cleanup_errors=True
        )
        if from_result is None and not dry_run
        else None
    )
    try:
        reviewer, reservation_id = _reserve_reviewer(
            root,
            selected_reviewer_id,
            latest_primary.reviewer_id if second_opinion and latest_primary else None,
            reserve=not dry_run and from_result is None,
            reserve_cursor_dry_run=dry_run,
        )
    except BaseException:
        if direct_workspace is not None:
            direct_workspace.cleanup()
        raise
    reservation_from_dry_run = False
    if reviewer.driver is ExternalReviewDriver.CLAUDE_CODE:
        prompt_builder = (
            _claude_minimal_file_prompt
            if CLAUDE_MINIMAL_FILE_PROTOCOL_ENABLED
            else _claude_prompt
        )
    elif reviewer.driver is ExternalReviewDriver.ANTIGRAVITY:
        prompt_builder = _antigravity_prompt
    else:
        prompt_builder = _cursor_prompt
    if from_result is not None:
        if dry_run_record is None or dry_run_record_path is None:  # pragma: no cover
            raise AssertionError("paired from-dry-run validation was skipped")
        result_path = from_result if from_result.is_absolute() else root / from_result
        try:
            if dry_run_reservation_id is None:  # pragma: no cover - validated above
                raise AssertionError("Cursor host dry-run reservation was not loaded")
            reservation_id = _claim_reviewer_reservation(
                root, dry_run_reservation_id, reviewer
            )
            reservation_from_dry_run = True
            imported_review_binding = _validate_cursor_host_dry_run(
                dry_run_record,
                dry_run_record_path,
                reviewer,
                batch_id=batch_id,
                second_opinion=second_opinion,
                fingerprint=fingerprint,
                scope=scope,
                base_run=base_run,
                covered_unit_ids=covered_unit_ids,
                read_only_context_ids=read_only_context_ids,
                packet_text=packet_text,
                pages=pages,
                reservation_id=reservation_id,
                context_fingerprint=context_fingerprint,
            )
            (
                payload,
                raw,
                requested_model,
                requested_effort,
                actual_model,
                fast_mode,
                attempts,
                prompt_delivery,
                duration_seconds,
                usage,
                cost_usd,
            ) = _load_cursor_host_result(
                reviewer,
                result_path,
                evidence_snapshot,
                imported_review_binding,
                cast(str, host_actual_model),
            )
        except BaseException:
            if reservation_from_dry_run:
                _restore_reviewer_reservation(root, reservation_id, reviewer)
            else:
                _release_reviewer_reservation(root, reservation_id)
            raise
    elif dry_run:
        try:
            return _create_external_review_dry_run(
                root,
                batch_id,
                reviewer,
                second_opinion=second_opinion,
                fingerprint=fingerprint,
                scope=scope,
                base_run=base_run,
                covered_unit_ids=covered_unit_ids,
                read_only_context_ids=read_only_context_ids,
                packet_text=packet_text,
                pages=pages,
                context_fingerprint=context_fingerprint,
                reservation_id=reservation_id,
                prompt_builder=prompt_builder,
            )
        except BaseException:
            _release_reviewer_reservation(root, reservation_id)
            raise
    # Some Windows reviewer CLIs briefly retain a handle to their working directory
    # after the parent process exits. Cleanup must not discard an otherwise valid,
    # fully parsed review result; any still-locked directory remains confined to the
    # OS temporary root and can be reclaimed after the child releases its handle.
    run_id = uuid.uuid4().hex
    reviewed_packet_sha256 = sha256_text(packet_text)
    if from_result is not None:
        reviewed_packet_sha256 = cast(
            str, cast(dict[str, Any], dry_run_record)["packet_sha256"]
        )
    if from_result is None:
        if direct_workspace is None:  # pragma: no cover - established above
            raise AssertionError("Direct external review workspace was not created")
        with direct_workspace as temp_name:
            work_dir = Path(temp_name)
            try:
                packet_path = _render_packet(
                    root, work_dir / "packet", packet_text, pages
                )
                with _provider_call_lock(root, reviewer):
                    (
                        payload,
                        raw,
                        requested_model,
                        requested_effort,
                        actual_model,
                        fast_mode,
                        attempts,
                        prompt_delivery,
                        duration_seconds,
                        usage,
                        cost_usd,
                    ) = _invoke(
                        reviewer, packet_path, work_dir, evidence_snapshot
                    )
                _persist_attempt_telemetry(root, batch_id, run_id, work_dir)
            except ExternalInvocationError as exc:
                try:
                    failure_cli_version = _command_version(reviewer.command)
                    _persist_attempt_telemetry(root, batch_id, run_id, work_dir)
                    with _external_persistence_lock(root, batch_id):
                        with project_write_lock(root):
                            response_dir = root / "reviews" / "external" / batch_id
                            response_dir.mkdir(parents=True, exist_ok=True)
                            raw_path = response_dir / f"{run_id}.raw.txt"
                            atomic_write_text(raw_path, exc.raw)
                            failed = ExternalReviewRun(
                                run_id=run_id,
                                batch_id=batch_id,
                                reviewer_id=reviewer.id,
                                driver=reviewer.driver,
                                role=(
                                    "second-opinion" if second_opinion else "primary"
                                ),
                                requested_model=reviewer.model,
                                model_verified=False,
                                cli_version=failure_cli_version,
                                effort=reviewer.effort,
                                translation_fingerprint=fingerprint,
                                packet_sha256=reviewed_packet_sha256,
                                prompt_version=PROMPT_VERSION,
                                scope=scope,
                                base_run_id=base_run.run_id if base_run else None,
                                covered_unit_ids=covered_unit_ids,
                                unit_fingerprints=current_unit_fingerprints,
                                source_fingerprint=source_fingerprint,
                                structure_fingerprint=structure_fingerprint,
                                context_fingerprint=context_fingerprint,
                                prompt_delivery=exc.prompt_delivery,
                                usage=exc.usage,
                                cost_usd=exc.cost_usd,
                                duration_seconds=exc.duration_seconds,
                                verdict=ExternalReviewVerdict.INCONCLUSIVE,
                                summary=f"[{exc.failure_type}] {exc}",
                                response_path=str(
                                    raw_path.relative_to(root)
                                ).replace("\\", "/"),
                                attempts=max(exc.attempts, 1),
                                failure_type=exc.failure_type,
                                fallback_of=_fallback_of,
                                attempt_log_path=(
                                    f"reviews/{batch_id}.external-attempts.jsonl"
                                ),
                                success=False,
                                reviewed_at=utc_now(),
                            )
                            append_jsonl(_runs_path(root, batch_id), [failed])
                            if _fallback_of:
                                _append_fallback_lineage_locked(
                                    root, batch_id, run_id, _fallback_of
                                )
                            status = external_review_status(root, batch_id)
                            write_json(
                                root / "reviews" / f"{batch_id}.external.json",
                                status,
                            )
                finally:
                    _release_reviewer_reservation(root, reservation_id)
                    reservation_id = None
                attempted = set(_attempted_reviewer_ids) | {reviewer.id}
                replacement = (
                    _select_replacement_reviewer(root, attempted)
                    if not second_opinion
                    else None
                )
                if replacement is not None:
                    return run_external_review(
                        root,
                        batch_id,
                        reviewer_id=replacement.id,
                        second_opinion=False,
                        _fallback_of=run_id,
                        _attempted_reviewer_ids=frozenset(attempted),
                    )
                return status
            except BaseException:
                _release_reviewer_reservation(root, reservation_id)
                reservation_id = None
                raise
    response_dir = root / "reviews" / "external" / batch_id
    raw_path = response_dir / f"{run_id}.raw.json"
    try:
        cli_version = (
            CURSOR_HOST_SUBAGENT_VERSION
            if from_result is not None
            else _command_version(reviewer.command)
        )
    except BaseException:
        if reservation_from_dry_run:
            _restore_reviewer_reservation(root, reservation_id, reviewer)
        else:
            _release_reviewer_reservation(root, reservation_id)
        reservation_id = None
        raise
    persistence_paths = [
        root / "reviews" / f"{batch_id}.issues.jsonl",
        root / "reviews" / f"{batch_id}.audit.json",
        root / "evidence" / "audits" / f"{batch_id}.jsonl",
        _runs_path(root, batch_id),
        root / "reviews" / f"{batch_id}.external.json",
        raw_path,
    ]
    if _fallback_of:
        persistence_paths.append(
            root / "reviews" / f"{batch_id}.external-fallbacks.jsonl"
        )
    persistence_snapshots: dict[Path, str | None] = {}
    import_path = root / "reviews" / f".external-import-{run_id}.jsonl"
    try:
        with _external_persistence_lock(root, batch_id):
            with project_write_lock(root):
                _require_review_snapshot_current_locked(
                    root,
                    batch_id,
                    covered_unit_ids,
                    scope,
                    fingerprint,
                    current_unit_fingerprints,
                    source_fingerprint,
                    structure_fingerprint,
                    context_fingerprint,
                )
                persistence_snapshots = _snapshot_text_files(persistence_paths)
                try:
                    issues = _convert_issues(
                        batch_id, reviewer, actual_model, fingerprint, run_id, payload
                    )
                    write_jsonl(import_path, issues)
                    _import_review_locked(root, batch_id, import_path, reviewer.id)
                    response_dir.mkdir(parents=True, exist_ok=True)
                    atomic_write_text(raw_path, raw)
                    run = ExternalReviewRun(
                        run_id=run_id,
                        batch_id=batch_id,
                        reviewer_id=reviewer.id,
                        driver=reviewer.driver,
                        role="second-opinion" if second_opinion else "primary",
                        requested_model=requested_model,
                        actual_model=actual_model,
                        actual_model_label=actual_model,
                        model_verified=True,
                        cli_version=cli_version,
                        effort=requested_effort,
                        fast_mode=fast_mode,
                        translation_fingerprint=fingerprint,
                        packet_sha256=reviewed_packet_sha256,
                        prompt_version=PROMPT_VERSION,
                        scope=scope,
                        base_run_id=base_run.run_id if base_run else None,
                        covered_unit_ids=covered_unit_ids,
                        unit_fingerprints=current_unit_fingerprints,
                        source_fingerprint=source_fingerprint,
                        structure_fingerprint=structure_fingerprint,
                        context_fingerprint=context_fingerprint,
                        duration_seconds=duration_seconds,
                        usage=usage,
                        cost_usd=cost_usd,
                        prompt_delivery=prompt_delivery,
                        verdict=ExternalReviewVerdict(payload["verdict"]),
                        summary=payload["summary"],
                        issue_ids=[issue.issue_id for issue in issues],
                        response_path=str(raw_path.relative_to(root)).replace(
                            "\\", "/"
                        ),
                        attempts=attempts,
                        fallback_of=_fallback_of,
                        attempt_log_path=(
                            None
                            if from_result is not None
                            else f"reviews/{batch_id}.external-attempts.jsonl"
                        ),
                        reviewed_at=utc_now(),
                    )
                    append_jsonl(_runs_path(root, batch_id), [run])
                    if _fallback_of:
                        _append_fallback_lineage_locked(
                            root, batch_id, run_id, _fallback_of
                        )
                    status = external_review_status(root, batch_id)
                    write_json(
                        root / "reviews" / f"{batch_id}.external.json", status
                    )
                except BaseException:
                    _restore_text_files(persistence_snapshots)
                    raise
    except BaseException:
        if reservation_from_dry_run:
            _restore_reviewer_reservation(root, reservation_id, reviewer)
        else:
            _release_reviewer_reservation(root, reservation_id)
        raise
    finally:
        import_path.unlink(missing_ok=True)
    _release_reviewer_reservation(root, reservation_id)
    reservation_id = None
    if (
        from_result is None
        and not second_opinion
        and status["second_opinion_required"]
    ):
        status = run_external_review(root, batch_id, second_opinion=True)
    return status
