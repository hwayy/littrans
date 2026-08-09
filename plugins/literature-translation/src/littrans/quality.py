from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from littrans.batching import load_manifest
from littrans.evidence import (
    audit_context_fingerprint,
    batch_unit_fingerprints,
    effective_figure_labels,
    source_representation_text,
)
from littrans.models import (
    PROJECT_SCHEMA_VERSION,
    AuditRun,
    BatchManifest,
    IssueStatus,
    ProjectStatus,
    QAItem,
    QAReport,
    ReviewIssue,
    ReviewScope,
    Severity,
    SourceUnit,
    UnitKind,
    utc_now,
)
from littrans.project import load_terms, promote_status, translation_map
from littrans.storage import (
    append_jsonl,
    load_project,
    project_write_lock,
    read_json,
    read_jsonl,
    require_current_project_schema,
    sha256_text,
    write_json,
    write_jsonl,
)
from littrans.verification import require_verified_extraction

NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9^])[-+]?\d+(?:[.,]\d+)?"
    r"(?:\s*[×x]\s*10\^?[-+]?\d+|\^[-+]?\d+)?"
)
UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9^])[-+]?\d+(?:\.\d+)?"
    r"(?:\s*[×x]\s*10\^?[-+]?\d+|\^[-+]?\d+)?\s*"
    r"(?:%|ms|s|kg|m|cm|mm|Hz|kHz|MHz|Pa|K|bar)\b"
)
MATH_OCR_SUSPECT_RE = re.compile(
    r"(?:\b(?:Re|R|St)\d+(?:/\d+)?\b|[ρντλ]\d+(?:/\d+)?\b|×\s*10\d{2,}\b)"
)
BLOCKING_SEVERITIES = {Severity.BLOCKER, Severity.MAJOR}
REQUIRED_AUDIT_LENSES = {"fidelity", "technical", "chinese-style"}
STATUS_ORDER = {
    status: index
    for index, status in enumerate(
        (
            ProjectStatus.INITIALIZED,
            ProjectStatus.EXTRACTED,
            ProjectStatus.PREPARED,
            ProjectStatus.DRAFT,
            ProjectStatus.REVISED,
            ProjectStatus.QA_PASSED,
            ProjectStatus.REVIEWED,
            ProjectStatus.MACHINE_REVIEWED,
            ProjectStatus.EXTERNAL_REVIEWED,
            ProjectStatus.HUMAN_APPROVED,
        )
    )
}
INLINE_LATEX_RE = re.compile(r"\$(?!\$)(.+?)(?<!\\)\$")
LATEX_COMMAND_TEXT = {
    "lambda": "λ",
    "rho": "ρ",
    "tau": "τ",
    "nu": "ν",
    "eta": "η",
    "varepsilon": "ε",
    "epsilon": "ε",
    "times": "×",
    "cdot": "·",
    "pm": "±",
}
SUPERSCRIPT_TEXT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
SUBSCRIPT_TEXT = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋", "0123456789+-")


def batch_translation_fingerprint(root: Path, batch_id: str) -> str:
    fingerprints = batch_unit_fingerprints(root, batch_id)
    return sha256_text(
        "\n".join(f"{unit_id}:{fingerprint}" for unit_id, fingerprint in fingerprints.items())
    )


def _qa_context_fingerprint(approved_terms: list[dict[str, Any]]) -> str:
    return sha256_text(
        "deterministic-qa-v4.1-figure-label-map|"
        + json.dumps(approved_terms, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def current_qa_context_fingerprint(root: Path) -> str:
    return _qa_context_fingerprint(load_terms(root))


def qa_report_is_current(root: Path, batch_id: str) -> bool:
    if load_project(root).schema_version != PROJECT_SCHEMA_VERSION:
        return False
    path = root / "qa" / f"{batch_id}.json"
    if not path.is_file():
        return False
    report = QAReport.model_validate(read_json(path))
    return bool(
        report.passed
        and report.translation_fingerprint == batch_translation_fingerprint(root, batch_id)
        and report.qa_context_fingerprint == current_qa_context_fingerprint(root)
    )


def _token_counts(pattern: re.Pattern[str], text: str) -> Counter[str]:
    return Counter(match.group(0).replace(" ", "") for match in pattern.finditer(text))


def _semantic_comparison_text(text: str) -> str:
    """Flatten verified LaTeX without weakening exact-LaTeX preservation checks."""
    value = text.replace("−", "-")
    value = re.sub(
        r"\b([23])\s*[-‐‑‒–—]?\s*[Dd]\b",
        lambda match: f"dimension-{match.group(1)}",
        value,
    )
    value = value.replace("二维", "dimension-2").replace("三维", "dimension-3")
    scale_patterns = (
        (re.compile(r"\b(\d+(?:\.\d+)?)\s+million\b", re.IGNORECASE), Decimal("1000000")),
        (re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*万"), Decimal("10000")),
    )
    for pattern, scale in scale_patterns:
        def replace_scaled(match: re.Match[str], factor: Decimal = scale) -> str:
            return _format_scaled_number(match.group(1), factor)

        value = pattern.sub(replace_scaled, value)
    value = re.sub(
        r"([⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+)",
        lambda match: "^" + match.group(1).translate(SUPERSCRIPT_TEXT),
        value,
    )
    value = re.sub(
        r"([₀₁₂₃₄₅₆₇₈₉₊₋]+)",
        lambda match: match.group(1).translate(SUBSCRIPT_TEXT),
        value,
    )
    # Unwrap common semantic styling/unit commands before removing braces.
    wrapper = re.compile(
        r"\\(?:mathrm|textrm|text|mathbf|boldsymbol|mathit|operatorname)\s*\{([^{}]*)\}"
    )
    previous = None
    while previous != value:
        previous = value
        value = wrapper.sub(r"\1", value)
        value = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"(\1)/(\2)", value)
    value = re.sub(r"\\[,;:! ]", " ", value)
    for command, replacement in LATEX_COMMAND_TEXT.items():
        value = re.sub(rf"\\{command}\b", replacement, value)
    value = re.sub(r"\^\s*\{([^{}]*)\}", r"^\1", value)
    value = re.sub(r"_\s*\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"_([A-Za-z0-9])", r"\1", value)
    value = value.replace("{", "").replace("}", "").replace("$", "")
    value = re.sub(r"\\([A-Za-z]+)", r"\1", value)
    return re.sub(r"\s+", " ", value).strip()


def _format_scaled_number(value: str, scale: Decimal) -> str:
    rendered = format(Decimal(value) * scale, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _semantic_token_present(token: str, raw_target: str, semantic_target: str) -> bool:
    if token in raw_target:
        return True
    normalized = _semantic_comparison_text(token)
    return bool(normalized and normalized in semantic_target)


def _comparison_source_text(unit: SourceUnit) -> str:
    text = unit.source_markdown or unit.source_text
    if unit.kind is UnitKind.LIST_ITEM:
        return re.sub(r"^\s*(?:[-+*•▪■●]\s+|\d+[.)]\s+)", "", text, count=1)
    return text


def _without_quoted_titles(text: str) -> str:
    return re.sub(r'["“][^"”]{2,}["”]', " ", text)


def _target_structure_error(unit: SourceUnit, target: str) -> str | None:
    if unit.kind is UnitKind.HEADING and re.match(r"^\s*#{1,6}\s+", target):
        return "Heading target must contain body text only; the renderer owns the heading marker."
    if unit.kind is UnitKind.LIST_ITEM and re.match(
        r"^\s*(?:[-+*•▪■●]\s+|\d+[.)]\s+)", target
    ):
        return "List-item target must contain body text only; the renderer owns the list marker."
    if unit.kind is UnitKind.NOTE and (
        re.match(r"^\s*>", target)
        or re.search(r"\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]", target, re.I)
        or re.match(
            r"^\s*(?:注意|提示|警告|新增内容|note|tip|warning|caution|what[’']s new)\s*[:：]",
            target,
            re.I,
        )
    ):
        return "Note target must contain body text only; the renderer owns the admonition shell."
    return None


def run_qa(root: Path, batch_id: str) -> QAReport:
    with project_write_lock(root):
        return _run_qa_locked(root, batch_id)


def _run_qa_locked(root: Path, batch_id: str) -> QAReport:
    config = require_current_project_schema(root, "Deterministic QA")
    manifest = load_manifest(root, batch_id)
    require_verified_extraction(root, set(manifest.pages))
    units = {
        unit.unit_id: unit for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    }
    translations = translation_map(root)
    errors: list[QAItem] = []
    warnings: list[QAItem] = []
    approved_terms = load_terms(root)
    fingerprint = batch_translation_fingerprint(root, batch_id)
    qa_context_fingerprint = _qa_context_fingerprint(approved_terms)
    existing_path = root / "qa" / f"{batch_id}.json"
    if existing_path.is_file():
        existing = QAReport.model_validate(read_json(existing_path))
        if (
            existing.passed
            and existing.translation_fingerprint == fingerprint
            and existing.qa_context_fingerprint == qa_context_fingerprint
        ):
            return existing

    for unit_id in manifest.translatable_unit_ids:
        unit = units[unit_id]
        record = translations.get(unit_id)
        if record is None:
            errors.append(
                QAItem(
                    code="missing-translation",
                    severity="error",
                    message="No translation was submitted.",
                    unit_id=unit_id,
                )
            )
            continue
        if record.source_hash != unit.source_hash:
            errors.append(
                QAItem(
                    code="source-hash-mismatch",
                    severity="error",
                    message="Translation targets a different source revision.",
                    unit_id=unit_id,
                )
            )
        effective_target = record.target_text
        if record.target_table:
            effective_target += "\n" + "\n".join(
                " | ".join(row) for row in record.target_table.rows
            )
        try:
            rendered_figure_labels = effective_figure_labels(unit, record)
        except ValueError as exc:
            errors.append(
                QAItem(
                    code="figure-label-mapping-mismatch",
                    severity="error",
                    message=str(exc),
                    unit_id=unit_id,
                )
            )
            rendered_figure_labels = unit.figure_labels
        if rendered_figure_labels:
            effective_target += "\n" + "\n".join(
                label.target or "" for label in rendered_figure_labels
            )
        effective_source = _comparison_source_text(unit)
        if rendered_figure_labels:
            effective_source += "\n" + "\n".join(
                label.source for label in rendered_figure_labels
            )
        semantic_source = _semantic_comparison_text(effective_source)
        semantic_target = _semantic_comparison_text(effective_target)
        if not effective_target.strip():
            errors.append(
                QAItem(
                    code="empty-translation",
                    severity="error",
                    message="Target text is empty.",
                    unit_id=unit_id,
                )
            )
        structural_error = _target_structure_error(unit, record.target_text)
        if structural_error:
            errors.append(
                QAItem(
                    code="target-structural-markup",
                    severity="error",
                    message=structural_error,
                    unit_id=unit_id,
                )
            )
        for token in unit.protected_tokens:
            if not _semantic_token_present(token, effective_target, semantic_target):
                errors.append(
                    QAItem(
                        code="protected-token-missing",
                        severity="error",
                        message=f"Protected token is missing: {token}",
                        unit_id=unit_id,
                    )
                )
        for name, pattern in (("number", NUMBER_RE), ("number-unit", UNIT_RE)):
            source_counts = _token_counts(pattern, semantic_source)
            target_counts = _token_counts(pattern, semantic_target)
            for token, count in source_counts.items():
                if target_counts[token] < count:
                    errors.append(
                        QAItem(
                            code=f"{name}-mismatch",
                            severity="error",
                            message=f"Expected {count} occurrence(s) of {token}; found {target_counts[token]}.",
                            unit_id=unit_id,
                        )
                    )
        source_folded = _without_quoted_titles(
            source_representation_text(unit)
        ).casefold()
        for term in approved_terms:
            source_term = str(term.get("source", ""))
            target_term = str(term.get("target", ""))
            scope = str(term.get("scope", "document"))
            if source_term and target_term and source_term.casefold() in source_folded:
                if scope == "document" or scope == f"page:{unit.page}" or scope == unit.parent_id:
                    if target_term not in effective_target:
                        errors.append(
                            QAItem(
                                code="approved-term-missing",
                                severity="error",
                                message=f"Approved translation required: {source_term} → {target_term}",
                                unit_id=unit_id,
                            )
                        )
            for forbidden in term.get("forbidden", []) or []:
                if str(forbidden) in effective_target:
                    errors.append(
                        QAItem(
                            code="forbidden-term",
                            severity="error",
                            message=f"Forbidden term used: {forbidden}",
                            unit_id=unit_id,
                        )
                    )
        source_length = max(len(unit.source_text), 1)
        ratio = len(effective_target) / source_length
        minimum_ratio = (
            0.15
            if config.source_language == "en" and config.target_language == "zh-CN"
            else 0.25
        )
        if ratio < minimum_ratio or ratio > 2.2:
            warnings.append(
                QAItem(
                    code="length-outlier",
                    severity="warning",
                    message=f"Target/source character ratio is {ratio:.2f}.",
                    unit_id=unit_id,
                )
            )
        if unit.kind is UnitKind.TABLE:
            if record.target_table is None:
                errors.append(
                    QAItem(
                        code="missing-target-table",
                        severity="error",
                        message="A structured source table requires a structured translated table.",
                        unit_id=unit_id,
                    )
                )
            elif unit.table and (
                record.target_table.column_count != unit.table.column_count
                or len(record.target_table.rows) != len(unit.table.rows)
            ):
                errors.append(
                    QAItem(
                        code="table-shape-mismatch",
                        severity="error",
                        message="Translated table dimensions differ from the verified source table.",
                        unit_id=unit_id,
                    )
                )
        source_math = INLINE_LATEX_RE.findall(unit.source_markdown or "")
        target_math = INLINE_LATEX_RE.findall(record.target_text)
        for latex in source_math:
            if latex not in target_math:
                errors.append(
                    QAItem(
                        code="inline-latex-missing",
                        severity="error",
                        message=f"Verified inline LaTeX is missing or changed: {latex}",
                        unit_id=unit_id,
                    )
                )
        if (
            "<!-- unit:" in record.target_text
            or "```json" in record.target_text
            or "![" in record.target_text
        ):
            errors.append(
                QAItem(
                    code="agent-analysis-leak",
                    severity="error",
                    message="Target text contains workflow, image, or analysis markup.",
                    unit_id=unit_id,
                )
            )
        if (
            config.source_language == "en"
            and config.target_language == "zh-CN"
            and unit.kind is not UnitKind.CODE
        ):
            source_words = re.findall(r"[A-Za-z]{2,}", unit.source_text)
            chinese_characters = re.findall(r"[\u3400-\u9fff]", record.target_text)
            if (
                len(unit.source_text) >= 80
                and len(source_words) >= 8
                and record.target_text.strip() == unit.source_text.strip()
            ):
                errors.append(
                    QAItem(
                        code="untranslated-target",
                        severity="error",
                        message="Target appears identical to a substantial English source unit.",
                        unit_id=unit_id,
                    )
                )
            elif len(source_words) >= 12 and not chinese_characters:
                warnings.append(
                    QAItem(
                        code="target-language-suspect",
                        severity="warning",
                        message="Long English source has no CJK characters in the target.",
                        unit_id=unit_id,
                    )
                )
        if MATH_OCR_SUSPECT_RE.search(unit.source_text):
            warnings.append(
                QAItem(
                    code="inline-math-ocr-suspect",
                    severity="warning",
                    message="Inline math may have lost superscript or operator formatting; compare the PDF.",
                    unit_id=unit_id,
                )
            )

    supplied_ids = set(translations)
    unknown = supplied_ids - set(units)
    for unit_id in sorted(unknown):
        errors.append(
            QAItem(
                code="unknown-unit",
                severity="error",
                message="Translation references an unknown source unit.",
                unit_id=unit_id,
            )
        )

    report = QAReport(
        batch_id=batch_id,
        passed=not errors,
        translation_fingerprint=fingerprint,
        qa_context_fingerprint=qa_context_fingerprint,
        errors=errors,
        warnings=warnings,
    )
    qa_dir = root / "qa"
    write_json(qa_dir / f"{batch_id}.json", report.model_dump(mode="json"))
    _write_qa_markdown(qa_dir / f"{batch_id}.md", report)
    if report.passed:
        current = translation_map(root)
        for unit_id in manifest.translatable_unit_ids:
            if STATUS_ORDER[current[unit_id].status] <= STATUS_ORDER[ProjectStatus.QA_PASSED]:
                current[unit_id] = current[unit_id].model_copy(
                    update={"status": ProjectStatus.QA_PASSED}
                )
        write_jsonl(root / "translations" / "current.jsonl", current.values())
        if STATUS_ORDER[load_project(root).status] <= STATUS_ORDER[ProjectStatus.QA_PASSED]:
            promote_status(root, ProjectStatus.QA_PASSED)
    return report


def _write_qa_markdown(path: Path, report: QAReport) -> None:
    lines = [
        f"# QA report: {report.batch_id}",
        "",
        f"Result: **{'PASS' if report.passed else 'FAIL'}**",
        f"Checked: {report.checked_at}",
        "",
        f"Errors: {len(report.errors)}",
        f"Warnings: {len(report.warnings)}",
        "",
    ]
    for heading, items in (("Errors", report.errors), ("Warnings", report.warnings)):
        lines.extend([f"## {heading}", ""])
        if not items:
            lines.append("None.\n")
        else:
            lines.extend(
                f"- `{item.code}`{f' ({item.unit_id})' if item.unit_id else ''}: {item.message}"
                for item in items
            )
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _audit_runs_path(root: Path, batch_id: str) -> Path:
    return root / "evidence" / "audits" / f"{batch_id}.jsonl"


def _audit_runs(root: Path, batch_id: str) -> list[AuditRun]:
    return read_jsonl(_audit_runs_path(root, batch_id), AuditRun)


def audit_coverage(root: Path, batch_id: str) -> dict[str, Any]:
    manifest = load_manifest(root, batch_id)
    current = batch_unit_fingerprints(root, batch_id)
    all_units = {
        unit.unit_id: unit
        for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    }
    expected = set(manifest.unit_ids)
    coverage: dict[str, set[str]] = {lens: set() for lens in REQUIRED_AUDIT_LENSES}
    context_fingerprints: dict[tuple[str, ...], str | None] = {}
    invalidation_path = (
        root / "evidence" / "audits" / f"{batch_id}.invalidations.json"
    )
    invalidated = (
        read_json(invalidation_path).get("units", {})
        if invalidation_path.is_file()
        else {}
    )
    if not isinstance(invalidated, dict):
        invalidated = {}
    for run in _audit_runs(root, batch_id):
        if run.lens not in coverage:
            continue
        context_ids = tuple(run.context_unit_ids)
        if not run.context_fingerprint or not context_ids:
            continue
        if context_ids not in context_fingerprints:
            context_fingerprints[context_ids] = (
                audit_context_fingerprint(
                    root, [all_units[unit_id] for unit_id in context_ids]
                )
                if all(unit_id in all_units for unit_id in context_ids)
                else None
            )
        if context_fingerprints[context_ids] != run.context_fingerprint:
            continue
        coverage[run.lens].update(
            unit_id
            for unit_id, fingerprint in run.unit_fingerprints.items()
            if unit_id in expected
            and current.get(unit_id) == fingerprint
            and run.reviewed_at > str(invalidated.get(unit_id, ""))
        )
    missing = {
        lens: sorted(expected - unit_ids) for lens, unit_ids in coverage.items()
    }
    return {
        "coverage": {lens: sorted(unit_ids) for lens, unit_ids in coverage.items()},
        "missing": missing,
        "complete": all(not unit_ids for unit_ids in missing.values()),
    }


def _write_audit_summary(root: Path, batch_id: str, issue_count: int) -> dict[str, Any]:
    coverage = audit_coverage(root, batch_id)
    complete_lenses = sorted(
        lens for lens, missing in coverage["missing"].items() if not missing
    )
    payload = {
        "batch_id": batch_id,
        "reviewed_at": utc_now(),
        "translation_fingerprint": batch_translation_fingerprint(root, batch_id),
        "lenses": complete_lenses,
        "unit_coverage": coverage["coverage"],
        "missing_coverage": coverage["missing"],
        "new_issue_count": issue_count,
    }
    write_json(root / "reviews" / f"{batch_id}.audit.json", payload)
    return payload


@dataclass
class _ReviewImportPlan:
    batch_id: str
    manifest: BatchManifest
    issues: list[ReviewIssue]
    merged_issues: list[ReviewIssue]
    internal_lenses: set[str]
    coverage_ids: set[str]
    fingerprints: dict[str, str]
    context_fingerprint: str | None
    context_unit_ids: list[str]
    preserve_status: bool
    reviewer: str | None
    packet_id: str | None


def _prepare_review_import_locked(
    root: Path,
    batch_id: str,
    issues: list[ReviewIssue],
    lenses: list[str] | None = None,
    preserve_status: bool = False,
    covered_unit_ids: list[str] | None = None,
    reviewer: str | None = None,
    packet_id: str | None = None,
    expected_unit_fingerprints: dict[str, str] | None = None,
    expected_context_fingerprint: str | None = None,
    context_unit_ids: list[str] | None = None,
) -> _ReviewImportPlan:
    require_current_project_schema(root, "Review import")
    manifest = load_manifest(root, batch_id)
    valid_units = set(manifest.unit_ids)
    issue_ids: set[str] = set()
    for issue in issues:
        if issue.batch_id != batch_id:
            raise ValueError(
                f"Issue {issue.issue_id} belongs to {issue.batch_id}, not {batch_id}"
            )
        if issue.unit_id not in valid_units:
            raise ValueError(f"Issue {issue.issue_id} references an invalid unit")
        if issue.issue_id in issue_ids:
            raise ValueError(f"Duplicate review issue ID: {issue.issue_id}")
        issue_ids.add(issue.issue_id)

    selected_lenses = set(REQUIRED_AUDIT_LENSES if lenses is None else lenses)
    internal_lenses = selected_lenses & REQUIRED_AUDIT_LENSES
    coverage_ids = set(
        manifest.unit_ids if covered_unit_ids is None else covered_unit_ids
    )
    invalid_coverage = coverage_ids - valid_units
    if invalid_coverage:
        raise ValueError(
            f"Audit coverage references invalid units: {sorted(invalid_coverage)}"
        )
    fingerprints = batch_unit_fingerprints(root, batch_id)
    if expected_unit_fingerprints is not None:
        if set(expected_unit_fingerprints) != coverage_ids:
            raise ValueError(
                "Expected audit fingerprints must match the covered unit IDs"
            )
        stale = sorted(
            unit_id
            for unit_id, expected in expected_unit_fingerprints.items()
            if fingerprints.get(unit_id) != expected
        )
        if stale:
            raise ValueError(f"Audit packet is stale for units: {stale}")
    run_context_ids: list[str] = []
    run_context_fingerprint: str | None = None
    if internal_lenses:
        run_context_ids = list(
            manifest.unit_ids if context_unit_ids is None else context_unit_ids
        )
        if len(run_context_ids) != len(set(run_context_ids)):
            raise ValueError("Audit context unit IDs must be unique")
        all_units = {
            unit.unit_id: unit
            for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
        }
        missing_context_units = sorted(set(run_context_ids) - set(all_units))
        if missing_context_units:
            raise ValueError(
                "Audit context references missing source units: "
                f"{missing_context_units}"
            )
        run_context_fingerprint = audit_context_fingerprint(
            root, [all_units[unit_id] for unit_id in run_context_ids]
        )
        if (
            expected_context_fingerprint is not None
            and run_context_fingerprint != expected_context_fingerprint
        ):
            raise ValueError("Audit packet context is stale")

    issue_path = root / "reviews" / f"{batch_id}.issues.jsonl"
    existing = {
        issue.issue_id: issue for issue in read_jsonl(issue_path, ReviewIssue)
    }
    conflicting_issue_ids = sorted(
        issue.issue_id
        for issue in issues
        if issue.issue_id in existing and existing[issue.issue_id] != issue
    )
    if conflicting_issue_ids:
        raise ValueError(
            "Review issue IDs already exist with different content: "
            f"{conflicting_issue_ids}"
        )
    existing.update({issue.issue_id: issue for issue in issues})
    return _ReviewImportPlan(
        batch_id=batch_id,
        manifest=manifest,
        issues=issues,
        merged_issues=list(existing.values()),
        internal_lenses=internal_lenses,
        coverage_ids=coverage_ids,
        fingerprints=fingerprints,
        context_fingerprint=run_context_fingerprint,
        context_unit_ids=run_context_ids,
        preserve_status=preserve_status,
        reviewer=reviewer,
        packet_id=packet_id,
    )


def _apply_review_import_locked(root: Path, plan: _ReviewImportPlan) -> list[ReviewIssue]:
    issue_path = root / "reviews" / f"{plan.batch_id}.issues.jsonl"
    write_jsonl(issue_path, plan.merged_issues)
    valid_units = set(plan.manifest.unit_ids)
    runs = _audit_runs(root, plan.batch_id)
    for lens in sorted(plan.internal_lenses):
        prior = next((run for run in reversed(runs) if run.lens == lens), None)
        run = AuditRun(
            run_id=uuid.uuid4().hex,
            batch_ids=[plan.batch_id],
            reviewer=plan.reviewer
            or (
                plan.issues[0].reviewer
                if plan.issues
                else "independent-auditor"
            ),
            lens=lens,
            scope=(
                ReviewScope.FULL
                if plan.coverage_ids >= valid_units
                else ReviewScope.INCREMENTAL
            ),
            base_run_id=prior.run_id if prior else None,
            packet_id=plan.packet_id,
            unit_fingerprints={
                unit_id: plan.fingerprints[unit_id]
                for unit_id in plan.manifest.unit_ids
                if unit_id in plan.coverage_ids
            },
            context_fingerprint=plan.context_fingerprint,
            context_unit_ids=plan.context_unit_ids,
            issue_ids=[issue.issue_id for issue in plan.issues],
        )
        append_jsonl(_audit_runs_path(root, plan.batch_id), [run])
        runs.append(run)
    if plan.internal_lenses or not (
        root / "reviews" / f"{plan.batch_id}.audit.json"
    ).exists():
        summary = _write_audit_summary(root, plan.batch_id, len(plan.issues))
        summary["total_issue_count"] = len(plan.merged_issues)
        write_json(root / "reviews" / f"{plan.batch_id}.audit.json", summary)
    if not plan.preserve_status:
        has_open_blocking = any(
            issue.status is IssueStatus.OPEN
            and issue.severity in BLOCKING_SEVERITIES
            for issue in plan.merged_issues
        )
        current = translation_map(root)
        for unit_id in plan.manifest.translatable_unit_ids:
            if has_open_blocking or (
                STATUS_ORDER[current[unit_id].status]
                <= STATUS_ORDER[ProjectStatus.REVIEWED]
            ):
                current[unit_id] = current[unit_id].model_copy(
                    update={"status": ProjectStatus.REVIEWED}
                )
        write_jsonl(root / "translations" / "current.jsonl", current.values())
        if has_open_blocking or (
            STATUS_ORDER[load_project(root).status]
            <= STATUS_ORDER[ProjectStatus.REVIEWED]
        ):
            promote_status(root, ProjectStatus.REVIEWED)
    return plan.merged_issues


def import_review(
    root: Path,
    batch_id: str,
    input_path: Path,
    lenses: list[str] | None = None,
    preserve_status: bool = False,
    covered_unit_ids: list[str] | None = None,
    reviewer: str | None = None,
    packet_id: str | None = None,
    expected_unit_fingerprints: dict[str, str] | None = None,
    expected_context_fingerprint: str | None = None,
    context_unit_ids: list[str] | None = None,
) -> list[ReviewIssue]:
    issues = read_jsonl(input_path, ReviewIssue)
    with project_write_lock(root):
        plan = _prepare_review_import_locked(
            root,
            batch_id,
            issues,
            lenses,
            preserve_status,
            covered_unit_ids,
            reviewer,
            packet_id,
            expected_unit_fingerprints,
            expected_context_fingerprint,
            context_unit_ids,
        )
        return _apply_review_import_locked(root, plan)


def resolve_issue(
    root: Path, batch_id: str, issue_id: str, status: IssueStatus, resolution: str
) -> ReviewIssue:
    require_current_project_schema(root, "Review issue resolution")
    load_manifest(root, batch_id)
    if status is IssueStatus.OPEN:
        raise ValueError("Resolved review issue status must not be open")
    if not resolution.strip():
        raise ValueError("Review issue resolution must not be empty")
    path = root / "reviews" / f"{batch_id}.issues.jsonl"
    issues = read_jsonl(path, ReviewIssue)
    resolved: ReviewIssue | None = None
    updated: list[ReviewIssue] = []
    for issue in issues:
        if issue.issue_id == issue_id:
            resolved = ReviewIssue.model_validate(
                {
                    **issue.model_dump(mode="json"),
                    "status": status,
                    "resolution": resolution.strip(),
                    "resolved_at": utc_now(),
                }
            )
            updated.append(resolved)
        else:
            updated.append(issue)
    if resolved is None:
        raise ValueError(f"Unknown review issue: {issue_id}")
    write_jsonl(path, updated)
    return resolved


def review_status(root: Path, batch_id: str) -> dict[str, Any]:
    load_manifest(root, batch_id)
    issues = read_jsonl(root / "reviews" / f"{batch_id}.issues.jsonl", ReviewIssue)
    counts = Counter(f"{issue.severity}:{issue.status}" for issue in issues)
    blocking = [
        issue.issue_id
        for issue in issues
        if issue.severity in BLOCKING_SEVERITIES and issue.status is IssueStatus.OPEN
    ]
    audit_path = root / "reviews" / f"{batch_id}.audit.json"
    coverage = audit_coverage(root, batch_id)
    lenses_complete = coverage["complete"]
    return {
        "batch_id": batch_id,
        "audit_exists": audit_path.exists(),
        "audit_lenses_complete": lenses_complete,
        "audit_coverage": coverage,
        "counts": dict(sorted(counts.items())),
        "open_blocking_issues": blocking,
        "publishable": not blocking and audit_path.exists() and lenses_complete,
    }


def approve_batch(
    root: Path,
    batch_id: str,
    level: str,
    confirm_user_approved: bool = False,
) -> ProjectStatus:
    if level not in {"machine", "external", "human"}:
        raise ValueError("level must be machine, external, or human")
    require_current_project_schema(root, "Batch approval")
    with project_write_lock(root):
        manifest = load_manifest(root, batch_id)
        require_verified_extraction(root, set(manifest.pages))
        qa_path = root / "qa" / f"{batch_id}.json"
        if not qa_path.exists():
            raise ValueError("A passing QA report is required")
        current_fingerprint = batch_translation_fingerprint(root, batch_id)
        qa_payload = json.loads(qa_path.read_text(encoding="utf-8"))
        if not qa_payload.get("passed"):
            raise ValueError("A passing QA report is required")
        if qa_payload.get("translation_fingerprint") != current_fingerprint:
            raise ValueError("The QA report is stale for the current translation revision")
        if qa_payload.get("qa_context_fingerprint") != current_qa_context_fingerprint(root):
            raise ValueError("The QA report is stale for the current approved terminology")
        status = review_status(root, batch_id)
        if not status["audit_exists"]:
            raise ValueError("An imported independent audit is required")
        coverage = audit_coverage(root, batch_id)
        if not coverage["complete"]:
            raise ValueError(
                "The independent audit is stale or incomplete; it must cover fidelity, "
                "technical, and Chinese-style lenses for every current source unit"
            )
        if status["open_blocking_issues"]:
            raise ValueError(f"Open blocker/major issues remain: {status['open_blocking_issues']}")
        if level == "human" and not confirm_user_approved:
            raise ValueError(
                "Human approval requires --confirm-user-approved after explicit user confirmation"
            )
        if level == "external":
            from littrans.external_review import external_review_status

            external = external_review_status(root, batch_id)
            if not external["external_approvable"]:
                raise ValueError(
                    "External approval gate is not satisfied: "
                    f"verdict={external['verdict']}, "
                    f"open_substantive_issues={external['open_substantive_issues']}"
                )

        target_status = {
            "machine": ProjectStatus.MACHINE_REVIEWED,
            "external": ProjectStatus.EXTERNAL_REVIEWED,
            "human": ProjectStatus.HUMAN_APPROVED,
        }[level]
        current = translation_map(root)
        changed = False
        for unit_id in manifest.translatable_unit_ids:
            if STATUS_ORDER[current[unit_id].status] < STATUS_ORDER[target_status]:
                current[unit_id] = current[unit_id].model_copy(
                    update={"status": target_status}
                )
                changed = True
        if changed:
            write_jsonl(root / "translations" / "current.jsonl", current.values())
        if STATUS_ORDER[load_project(root).status] < STATUS_ORDER[target_status]:
            promote_status(root, target_status)
    return target_status
