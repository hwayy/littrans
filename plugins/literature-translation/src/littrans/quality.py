from __future__ import annotations

import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from littrans.batching import load_manifest
from littrans.models import (
    IssueStatus,
    ProjectStatus,
    QAItem,
    QAReport,
    ReviewIssue,
    Severity,
    SourceUnit,
    UnitKind,
    utc_now,
)
from littrans.project import load_terms, promote_status, translation_map
from littrans.storage import (
    load_project,
    project_write_lock,
    read_jsonl,
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
    manifest = load_manifest(root, batch_id)
    translations = translation_map(root)
    units = {
        unit.unit_id: unit for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    }
    material: list[str] = []
    for unit_id in manifest.unit_ids:
        unit = units[unit_id]
        semantic = unit.model_dump_json(
            include={
                "kind",
                "source_hash",
                "source_markdown",
                "translatable",
                "render_policy",
                "protected_tokens",
                "latex",
                "equation_number",
                "math_status",
                "table",
                "code_language",
                "continues_from_previous",
                "continued_to_next",
                "figure_labels",
                "verification_status",
            },
            exclude_none=True,
        )
        source_fingerprint = sha256_text(semantic)
        if not unit.translatable:
            material.append(f"{unit_id}|source-only|{source_fingerprint}")
            continue
        record = translations.get(unit_id)
        if record is None:
            material.append(f"{unit_id}|{source_fingerprint}|missing")
        else:
            material.append(
                f"{unit_id}|{record.source_hash}|{source_fingerprint}|{record.revision}|"
                f"{sha256_text(record.model_dump_json(include={'target_text', 'target_table', 'figure_labels', 'reader_note', 'term_proposals', 'uncertainties'}, exclude_none=True))}"
            )
    return sha256_text("\n".join(material))


def _token_counts(pattern: re.Pattern[str], text: str) -> Counter[str]:
    return Counter(match.group(0).replace(" ", "") for match in pattern.finditer(text))


def _semantic_comparison_text(text: str) -> str:
    """Flatten verified LaTeX without weakening exact-LaTeX preservation checks."""
    value = text.replace("−", "-")
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
        or re.search(r"\[!(?:NOTE|TIP|WARNING|CAUTION)\]", target, re.I)
        or re.match(
            r"^\s*(?:注意|提示|警告|note|tip|warning|caution)\s*[:：]", target, re.I
        )
    ):
        return "Note target must contain body text only; the renderer owns the admonition shell."
    return None


def run_qa(root: Path, batch_id: str) -> QAReport:
    config = load_project(root)
    manifest = load_manifest(root, batch_id)
    require_verified_extraction(root, set(manifest.pages))
    units = {
        unit.unit_id: unit for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    }
    translations = translation_map(root)
    errors: list[QAItem] = []
    warnings: list[QAItem] = []
    approved_terms = load_terms(root)

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
        semantic_source = _semantic_comparison_text(unit.source_markdown or unit.source_text)
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
        source_folded = _without_quoted_titles(unit.source_text).casefold()
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
        translation_fingerprint=batch_translation_fingerprint(root, batch_id),
        errors=errors,
        warnings=warnings,
    )
    qa_dir = root / "qa"
    write_json(qa_dir / f"{batch_id}.json", report.model_dump(mode="json"))
    _write_qa_markdown(qa_dir / f"{batch_id}.md", report)
    if report.passed:
        with project_write_lock(root):
            current = translation_map(root)
            for unit_id in manifest.translatable_unit_ids:
                current[unit_id] = current[unit_id].model_copy(
                    update={"status": ProjectStatus.QA_PASSED}
                )
            write_jsonl(root / "translations" / "current.jsonl", current.values())
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


def import_review(
    root: Path,
    batch_id: str,
    input_path: Path,
    lenses: list[str] | None = None,
) -> list[ReviewIssue]:
    manifest = load_manifest(root, batch_id)
    issues = read_jsonl(input_path, ReviewIssue)
    valid_units = set(manifest.unit_ids)
    issue_ids: set[str] = set()
    for issue in issues:
        if issue.batch_id != batch_id:
            raise ValueError(f"Issue {issue.issue_id} belongs to {issue.batch_id}, not {batch_id}")
        if issue.unit_id not in valid_units:
            raise ValueError(f"Issue {issue.issue_id} references an invalid unit")
        if issue.issue_id in issue_ids:
            raise ValueError(f"Duplicate review issue ID: {issue.issue_id}")
        issue_ids.add(issue.issue_id)
    issue_path = root / "reviews" / f"{batch_id}.issues.jsonl"
    existing = {issue.issue_id: issue for issue in read_jsonl(issue_path, ReviewIssue)}
    existing.update({issue.issue_id: issue for issue in issues})
    merged_issues = list(existing.values())
    write_jsonl(issue_path, merged_issues)
    write_json(
        root / "reviews" / f"{batch_id}.audit.json",
        {
            "batch_id": batch_id,
            "reviewed_at": utc_now(),
            "translation_fingerprint": batch_translation_fingerprint(root, batch_id),
            "lenses": lenses or ["fidelity", "technical", "chinese-style"],
            "new_issue_count": len(issues),
            "total_issue_count": len(merged_issues),
        },
    )
    with project_write_lock(root):
        current = translation_map(root)
        for unit_id in manifest.translatable_unit_ids:
            current[unit_id] = current[unit_id].model_copy(
                update={"status": ProjectStatus.REVIEWED}
            )
        write_jsonl(root / "translations" / "current.jsonl", current.values())
        promote_status(root, ProjectStatus.REVIEWED)
    return merged_issues


def resolve_issue(
    root: Path, batch_id: str, issue_id: str, status: IssueStatus, resolution: str
) -> ReviewIssue:
    path = root / "reviews" / f"{batch_id}.issues.jsonl"
    issues = read_jsonl(path, ReviewIssue)
    resolved: ReviewIssue | None = None
    updated: list[ReviewIssue] = []
    for issue in issues:
        if issue.issue_id == issue_id:
            resolved = issue.model_copy(
                update={"status": status, "resolution": resolution, "resolved_at": utc_now()}
            )
            updated.append(resolved)
        else:
            updated.append(issue)
    if resolved is None:
        raise ValueError(f"Unknown review issue: {issue_id}")
    write_jsonl(path, updated)
    return resolved


def review_status(root: Path, batch_id: str) -> dict[str, Any]:
    issues = read_jsonl(root / "reviews" / f"{batch_id}.issues.jsonl", ReviewIssue)
    counts = Counter(f"{issue.severity}:{issue.status}" for issue in issues)
    blocking = [
        issue.issue_id
        for issue in issues
        if issue.severity in BLOCKING_SEVERITIES and issue.status is IssueStatus.OPEN
    ]
    audit_path = root / "reviews" / f"{batch_id}.audit.json"
    audit_payload = (
        json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
    )
    lenses_complete = REQUIRED_AUDIT_LENSES.issubset(set(audit_payload.get("lenses", [])))
    return {
        "batch_id": batch_id,
        "audit_exists": audit_path.exists(),
        "audit_lenses_complete": lenses_complete,
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
    if level not in {"machine", "human"}:
        raise ValueError("level must be machine or human")
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
    status = review_status(root, batch_id)
    if not status["audit_exists"]:
        raise ValueError("An imported independent audit is required")
    audit_payload = json.loads(
        (root / "reviews" / f"{batch_id}.audit.json").read_text(encoding="utf-8")
    )
    if not REQUIRED_AUDIT_LENSES.issubset(set(audit_payload.get("lenses", []))):
        raise ValueError(
            "The independent audit must cover fidelity, technical, and Chinese-style lenses"
        )
    if audit_payload.get("translation_fingerprint") != current_fingerprint:
        raise ValueError("The independent audit is stale for the current translation revision")
    if status["open_blocking_issues"]:
        raise ValueError(f"Open blocker/major issues remain: {status['open_blocking_issues']}")
    if level == "human" and not confirm_user_approved:
        raise ValueError(
            "Human approval requires --confirm-user-approved after explicit user confirmation"
        )

    target_status = (
        ProjectStatus.HUMAN_APPROVED if level == "human" else ProjectStatus.MACHINE_REVIEWED
    )
    with project_write_lock(root):
        current = translation_map(root)
        for unit_id in manifest.translatable_unit_ids:
            current[unit_id] = current[unit_id].model_copy(update={"status": target_status})
        write_jsonl(root / "translations" / "current.jsonl", current.values())
        promote_status(root, target_status)
    return target_status
