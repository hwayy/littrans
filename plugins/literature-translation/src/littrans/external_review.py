from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, cast

import fitz
import yaml

from littrans.batching import load_manifest
from littrans.evidence import (
    batch_source_fingerprint,
    batch_structure_fingerprint,
    batch_unit_fingerprints,
    changed_units,
    dependency_closure,
    effective_figure_labels,
    relevant_terms,
)
from littrans.models import (
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
    audit_coverage,
    batch_translation_fingerprint,
    import_review,
    qa_report_is_current,
)
from littrans.semantics import normalize_zh_caption
from littrans.storage import (
    append_jsonl,
    atomic_write_text,
    load_project,
    read_jsonl,
    require_current_project_schema,
    sha256_text,
    write_json,
    write_jsonl,
)
from littrans.verification import require_verified_extraction

PROMPT_VERSION = "external-review-v3"
# The 0.3.0 shadow gate showed excellent efficiency but missed a seeded major
# technical defect. Keep the implementation available for future experiments,
# while production reviews remain on the proven file-delivery path.
CLAUDE_STDIN_PROMPT_DELIVERY_ENABLED = False
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
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.raw = raw
        self.prompt_delivery = prompt_delivery
        self.usage = usage or ReviewUsage()
        self.cost_usd = cost_usd
        self.duration_seconds = duration_seconds


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
) -> list[str]:
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


def _outer_seam_context_ids(
    root: Path, batch_id: str, covered_unit_ids: list[str]
) -> list[str]:
    manifest = load_manifest(root, batch_id)
    covered = set(covered_unit_ids)
    reached_seams = [
        unit_id
        for unit_id in (manifest.unit_ids[0], manifest.unit_ids[-1])
        if unit_id in covered
    ]
    if not reached_seams:
        return []
    manifest_ids = set(manifest.unit_ids)
    return [
        unit_id
        for unit_id in dependency_closure(root, [batch_id], reached_seams)
        if unit_id not in manifest_ids
    ]


def _packet_text(
    root: Path,
    batch_id: str,
    covered_unit_ids: list[str] | None = None,
    translation_overrides: dict[str, TranslationRecord] | None = None,
    compact: bool = True,
    read_only_context_ids: list[str] | None = None,
) -> tuple[str, list[int]]:
    manifest = load_manifest(root, batch_id)
    project = load_project(root)
    domain_expertise = (
        project.external_review.domain_expertise
        if project.external_review and project.external_review.domain_expertise
        else (
            "Infer the subject matter and the technical or scholarly expertise required "
            "from the document brief."
        )
    )
    all_units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    units = {unit.unit_id: unit for unit in all_units}
    translations = translation_map(root)
    if translation_overrides:
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
    packet_ids = [
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
    selected_units = [units[unit_id] for unit_id in packet_ids]
    sections: list[str] = []
    for unit_id in packet_ids:
        unit = units[unit_id]
        record = translations.get(unit_id)
        source = unit.source_markdown or unit.source_text
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
        labels = effective_figure_labels(unit, record)
        if unit.kind is UnitKind.FIGURE and labels:
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
    brief = (root / "context" / "document-brief.md").read_text(encoding="utf-8")
    style = (root / "context" / "style-guide.md").read_text(encoding="utf-8")
    terms = yaml.safe_dump(
        {
            "approved_terms": (
                relevant_terms(root, selected_units) if compact else load_terms(root)
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
    return text, sorted({unit.page for unit in selected_units})


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
        source = unit.source_markdown or unit.source_text
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


def _strip_json_wrapping(text: str) -> str:
    value = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", value, re.S | re.I)
    if fenced:
        return fenced.group(1)
    start, end = value.find("{"), value.rfind("}")
    return value[start : end + 1] if start >= 0 and end > start else value


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
    return _validate_result(json.loads(_strip_json_wrapping(stdout))), actual


def _normalized_model(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold()).replace("thinking", "")


def _model_matches(requested: str | None, actual: str | None) -> bool:
    requested_norm = _normalized_model(requested)
    actual_norm = _normalized_model(actual)
    return bool(actual_norm and (requested_norm in actual_norm or actual_norm in requested_norm))


def _command_version(command: str) -> str | None:
    try:
        result = subprocess.run(
            [command, "--version"], capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout or result.stderr).strip().splitlines()[0] or None


def _review_usage(raw: str, driver: ExternalReviewDriver) -> tuple[ReviewUsage, float | None]:
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
    return (
        ReviewUsage(
            input_tokens=int(usage_payload.get("input_tokens") or 0),
            cache_creation_input_tokens=int(
                usage_payload.get("cache_creation_input_tokens") or 0
            ),
            cache_read_input_tokens=int(usage_payload.get("cache_read_input_tokens") or 0),
            output_tokens=int(usage_payload.get("output_tokens") or 0),
            provider_turns=int(usage_payload.get("provider_turns") or 0),
        ),
        float(outer["cost_usd"]) if outer.get("cost_usd") is not None else None,
    )


def _invoke(
    reviewer: ExternalReviewerConfig,
    packet_path: Path,
    work_dir: Path,
    evidence: dict[str, tuple[str, str]],
    forced_delivery: PromptDelivery | None = None,
    file_prompt: str | None = None,
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
    started = time.perf_counter()
    for model, effort in candidates:
        candidate = reviewer.model_copy(update={"model": model, "effort": effort})
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
            prompt = (
                (file_prompt or _claude_prompt(packet_path))
                if candidate.driver is ExternalReviewDriver.CLAUDE_CODE
                else _antigravity_prompt(packet_path)
            )
            stdin_text = _claude_stdin(packet_path) if delivery is PromptDelivery.STDIN else None
            for format_attempt in range(2):
                attempts += 1
                log_path = work_dir / f"driver-{attempts}.log"
                command = (
                    build_claude_command(
                        candidate, "" if delivery is PromptDelivery.STDIN else prompt
                    )
                    if candidate.driver is ExternalReviewDriver.CLAUDE_CODE
                    else build_antigravity_command(candidate, prompt, log_path)
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
                        timeout=330,
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
                    errors.append(
                        f"external CLI timed out after {exc.timeout} seconds "
                        f"for model={model}, delivery={delivery.value}"
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
                    else:
                        payload, actual_label = _parse_antigravity(
                            result.stdout, log_text
                        )
                        verified = _model_matches(model, actual_label)
                        actual_model = actual_label
                        fast_mode = None
                    if not verified:
                        raise RuntimeError(
                            "actual model could not be verified: "
                            f"requested={model}, actual={actual_label}"
                        )
                    _validate_issue_evidence(payload, evidence)
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
                    if format_attempt == 0:
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
    )


def _runs_path(root: Path, batch_id: str) -> Path:
    return root / "reviews" / f"{batch_id}.external-runs.jsonl"


def _all_runs(root: Path) -> list[ExternalReviewRun]:
    runs: list[ExternalReviewRun] = []
    for path in (root / "reviews").glob("*.external-runs.jsonl"):
        runs.extend(read_jsonl(path, ExternalReviewRun))
    return runs


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
    usage = external_reviewer_usage(root)
    return min(
        candidates,
        key=lambda reviewer: (
            usage[reviewer.id]["assigned_primary_batches"],
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


def _primary_chain_approvable(
    root: Path,
    runs: list[ExternalReviewRun],
    primary: ExternalReviewRun,
    seen: set[str] | None = None,
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
        return bool(
            inherited
            and _primary_chain_approvable(root, runs, inherited, visited)
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


def external_review_status(root: Path, batch_id: str) -> dict[str, Any]:
    _review_config(root)
    fingerprint = batch_translation_fingerprint(root, batch_id)
    all_runs = read_jsonl(_runs_path(root, batch_id), ExternalReviewRun)
    runs = [
        run
        for run in all_runs
        if run.translation_fingerprint == fingerprint
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
    needs_second = bool(primary and _needs_second_opinion(root, primary))
    if primary is None:
        verdict = "missing"
    elif not _primary_chain_approvable(root, all_runs, primary):
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
        "reviewer_usage": external_reviewer_usage(root),
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
        if not chain_approvable:
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
) -> dict[str, Any]:
    require_current_project_schema(root, "External review")
    _require_machine_reviewed(
        root, batch_id, allow_external_issues=second_opinion
    )
    fingerprint = batch_translation_fingerprint(root, batch_id)
    if not second_opinion and not dry_run:
        current_status = external_review_status(root, batch_id)
        if current_status["external_approvable"]:
            return current_status
    primary_runs = [
        run
        for run in read_jsonl(_runs_path(root, batch_id), ExternalReviewRun)
        if run.translation_fingerprint == fingerprint
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
        covered_unit_ids = list(latest_primary.covered_unit_ids)
        selected_reviewer_id = reviewer_id
    else:
        scope, base_run, covered_unit_ids, selected_reviewer_id = _primary_review_scope(
            root, batch_id, reviewer_id
        )
    reviewer = _select_reviewer(
        root,
        selected_reviewer_id,
        latest_primary.reviewer_id if second_opinion and latest_primary else None,
    )
    read_only_context_ids = (
        _outer_seam_context_ids(root, batch_id, covered_unit_ids)
        if scope is ReviewScope.INCREMENTAL
        else []
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
    prompt_builder = (
        _claude_prompt
        if reviewer.driver is ExternalReviewDriver.CLAUDE_CODE
        else _antigravity_prompt
    )
    if dry_run:
        work_dir = (
            root
            / "reviews"
            / "external-dry-run"
            / batch_id
            / f"{reviewer.id}-{uuid.uuid4().hex[:8]}"
        )
        packet_path = _render_packet(root, work_dir / "packet", packet_text, pages)
        prompt = prompt_builder(packet_path)
        log_path = work_dir / "driver.log"
        command = (
            build_claude_command(reviewer, prompt)
            if reviewer.driver is ExternalReviewDriver.CLAUDE_CODE
            else build_antigravity_command(reviewer, prompt, log_path)
        )
        write_json(
            work_dir / "dry-run.json",
            {
                "batch_id": batch_id,
                "reviewer_id": reviewer.id,
                "driver": reviewer.driver,
                "translation_fingerprint": fingerprint,
                "scope": scope,
                "base_run_id": base_run.run_id if base_run else None,
                "covered_unit_ids": covered_unit_ids,
                "read_only_context_unit_ids": read_only_context_ids,
                "packet_sha256": sha256_text(packet_text),
                "packet_path": str(packet_path),
                "prompt": prompt,
                "command": command,
                "executed": False,
                "prompt_delivery": PromptDelivery.FILE,
            },
        )
        dry_run_payload = json.loads((work_dir / "dry-run.json").read_text(encoding="utf-8"))
        if not isinstance(dry_run_payload, dict):
            raise ValueError("External review dry-run record must be a JSON object")
        return cast(dict[str, Any], dry_run_payload)
    # Some Windows reviewer CLIs briefly retain a handle to their working directory
    # after the parent process exits. Cleanup must not discard an otherwise valid,
    # fully parsed review result; any still-locked directory remains confined to the
    # OS temporary root and can be reclaimed after the child releases its handle.
    with tempfile.TemporaryDirectory(
        prefix=f"littrans-{batch_id}-", ignore_cleanup_errors=True
    ) as temp_name:
        work_dir = Path(temp_name)
        packet_path = _render_packet(root, work_dir / "packet", packet_text, pages)
        prompt = prompt_builder(packet_path)
        log_path = work_dir / "driver.log"
        command = (
            build_claude_command(reviewer, prompt)
            if reviewer.driver is ExternalReviewDriver.CLAUDE_CODE
            else build_antigravity_command(reviewer, prompt, log_path)
        )
        try:
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
                reviewer,
                packet_path,
                work_dir,
                _evidence_map(
                    root, batch_id, covered_unit_ids=covered_unit_ids
                ),
            )
        except ExternalInvocationError as exc:
            run_id = uuid.uuid4().hex
            response_dir = root / "reviews" / "external" / batch_id
            response_dir.mkdir(parents=True, exist_ok=True)
            raw_path = response_dir / f"{run_id}.raw.txt"
            atomic_write_text(raw_path, exc.raw)
            failed = ExternalReviewRun(
                run_id=run_id,
                batch_id=batch_id,
                reviewer_id=reviewer.id,
                driver=reviewer.driver,
                role="second-opinion" if second_opinion else "primary",
                requested_model=reviewer.model,
                model_verified=False,
                cli_version=_command_version(reviewer.command),
                effort=reviewer.effort,
                translation_fingerprint=fingerprint,
                packet_sha256=sha256_text(packet_text),
                prompt_version=PROMPT_VERSION,
                scope=scope,
                base_run_id=base_run.run_id if base_run else None,
                covered_unit_ids=covered_unit_ids,
                unit_fingerprints=current_unit_fingerprints,
                source_fingerprint=source_fingerprint,
                structure_fingerprint=structure_fingerprint,
                prompt_delivery=exc.prompt_delivery,
                usage=exc.usage,
                cost_usd=exc.cost_usd,
                duration_seconds=exc.duration_seconds,
                verdict=ExternalReviewVerdict.INCONCLUSIVE,
                summary=str(exc),
                response_path=str(raw_path.relative_to(root)).replace("\\", "/"),
                attempts=max(exc.attempts, 1),
                success=False,
                reviewed_at=utc_now(),
            )
            append_jsonl(_runs_path(root, batch_id), [failed])
            status = external_review_status(root, batch_id)
            write_json(root / "reviews" / f"{batch_id}.external.json", status)
            if not second_opinion and len(_review_config(root).reviewers) > 1:
                return run_external_review(root, batch_id, second_opinion=True)
            return status
    run_id = uuid.uuid4().hex
    issues = _convert_issues(
        batch_id, reviewer, actual_model, fingerprint, run_id, payload
    )
    import_path = root / "reviews" / f".external-import-{run_id}.jsonl"
    write_jsonl(import_path, issues)
    try:
        import_review(
            root,
            batch_id,
            import_path,
            [f"external:{reviewer.id}"],
            preserve_status=True,
        )
    finally:
        import_path.unlink(missing_ok=True)
    response_dir = root / "reviews" / "external" / batch_id
    response_dir.mkdir(parents=True, exist_ok=True)
    raw_path = response_dir / f"{run_id}.raw.json"
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
        cli_version=_command_version(reviewer.command),
        effort=requested_effort,
        fast_mode=fast_mode,
        translation_fingerprint=fingerprint,
        packet_sha256=sha256_text(packet_text),
        prompt_version=PROMPT_VERSION,
        scope=scope,
        base_run_id=base_run.run_id if base_run else None,
        covered_unit_ids=covered_unit_ids,
        unit_fingerprints=current_unit_fingerprints,
        source_fingerprint=source_fingerprint,
        structure_fingerprint=structure_fingerprint,
        duration_seconds=duration_seconds,
        usage=usage,
        cost_usd=cost_usd,
        prompt_delivery=prompt_delivery,
        verdict=ExternalReviewVerdict(payload["verdict"]),
        summary=payload["summary"],
        issue_ids=[issue.issue_id for issue in issues],
        response_path=str(raw_path.relative_to(root)).replace("\\", "/"),
        attempts=attempts,
        reviewed_at=utc_now(),
    )
    append_jsonl(_runs_path(root, batch_id), [run])
    status = external_review_status(root, batch_id)
    write_json(root / "reviews" / f"{batch_id}.external.json", status)
    if not second_opinion and status["second_opinion_required"]:
        status = run_external_review(root, batch_id, second_opinion=True)
    return status
