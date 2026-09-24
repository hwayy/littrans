from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from littrans.hosts import (
    WAVE_BATCH_SET_MAX,
    host_model_defaults,
    normalize_agent_models,
)

BATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
BatchId = Annotated[str, Field(pattern=BATCH_ID_PATTERN.pattern)]
PROJECT_SCHEMA_VERSION = 6


def validate_batch_identifier(value: str) -> str:
    if BATCH_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("batch ID must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    return value


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectStatus(StrEnum):
    INITIALIZED = "initialized"
    EXTRACTED = "extracted"
    PREPARED = "prepared"
    DRAFT = "draft"
    QA_PASSED = "qa-passed"
    REVIEWED = "reviewed"
    REVISED = "revised"
    MACHINE_REVIEWED = "machine-reviewed"
    EXTERNAL_REVIEWED = "external-reviewed"
    HUMAN_APPROVED = "human-approved"


class UnitKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    NOTE = "note"
    CODE = "code"
    EQUATION = "equation"
    FIGURE = "figure"
    CAPTION = "caption"
    TABLE = "table"
    FOOTNOTE = "footnote"
    BIBLIOGRAPHY = "bibliography"


class SidebarRole(StrEnum):
    TITLE = "title"
    BODY = "body"


class CalloutKind(StrEnum):
    NOTE = "note"
    TIP = "tip"
    WARNING = "warning"
    CAUTION = "caution"
    WHATS_NEW = "whats-new"


class SemanticStatus(StrEnum):
    UNVERIFIED = "unverified"
    AUTO = "auto"
    VERIFIED = "verified"


class RenderPolicy(StrEnum):
    INCLUDE = "include"
    OMIT = "omit"


class Severity(StrEnum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    SUGGESTION = "suggestion"


class IssueType(StrEnum):
    MEANING = "meaning"
    OMISSION = "omission"
    ADDITION = "addition"
    TERMINOLOGY = "terminology"
    TECHNICAL = "technical"
    STYLE = "style"
    REFERENCE = "reference"
    NUMBER_UNIT = "number-unit"
    FORMAT = "format"


class IssueStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    WAIVED = "waived"


class ExternalReviewDriver(StrEnum):
    CLAUDE_CODE = "claude-code"
    ANTIGRAVITY = "antigravity"
    CURSOR_CLI = "cursor-cli"


class ExternalReviewVerdict(StrEnum):
    ACCEPTED = "accepted"
    CHANGES_REQUESTED = "changes-requested"
    INCONCLUSIVE = "inconclusive"


class ReviewScope(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"


class PromptDelivery(StrEnum):
    STDIN = "stdin"
    FILE = "file"


class MathCandidateClassification(StrEnum):
    INLINE = "inline"
    DISPLAY = "display"
    MIXED = "mixed"
    NOT_MATH = "not-math"
    NEEDS_SPLIT = "needs-split"


class MathReviewDisposition(StrEnum):
    ACCEPTED = "accepted"
    CORRECTED = "corrected"
    MANUAL = "manual"
    REJECTED = "rejected"


class MathStructuralAction(StrEnum):
    """Closed set of structural changes a math-review sidecar may request."""

    IGNORE = "ignore"
    MERGE = "merge"
    SPLIT = "split"
    RECLASSIFY = "reclassify"
    REORDER = "reorder"


MathStructuralField = Literal[
    "kind",
    "source_text",
    "source_markdown",
    "latex",
    "equation_number",
    "set_bbox",
    "parent_id",
    "continues_from_previous",
    "continued_to_next",
]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


_MODEL_IDENTITY_DESCRIPTION = (
    "Concrete model the host must report as served for `model`. Set it when `model` is a host "
    "alias (for example `sonnet`) that the host routes to another model; when unset, `model` "
    "itself is the identity that host evidence must match."
)


def _nonempty_model_identity(value: str | None) -> str | None:
    if value is not None and not value.strip():
        raise ValueError("external reviewer model_identity must not be empty when set")
    return value


class ExternalReviewFallback(StrictModel):
    model: str = Field(description="Dispatch value passed to the provider CLI.")
    model_identity: str | None = Field(default=None, description=_MODEL_IDENTITY_DESCRIPTION)
    effort: str | None = None

    @field_validator("model_identity")
    @classmethod
    def require_nonempty_fallback_model_identity(cls, value: str | None) -> str | None:
        return _nonempty_model_identity(value)


class ExternalReviewerConfig(StrictModel):
    id: str
    driver: ExternalReviewDriver
    command: str
    model: str = Field(description="Dispatch value passed to the provider CLI's model option.")
    model_identity: str | None = Field(default=None, description=_MODEL_IDENTITY_DESCRIPTION)
    effort: str | None = None
    fast: bool | None = None
    fallbacks: list[ExternalReviewFallback] = Field(default_factory=list)

    @field_validator("id", "command", "model")
    @classmethod
    def require_nonempty_external_reviewer_values(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("external reviewer values must not be empty")
        return value

    @field_validator("model_identity")
    @classmethod
    def require_nonempty_model_identity(cls, value: str | None) -> str | None:
        return _nonempty_model_identity(value)

    @model_validator(mode="after")
    def validate_driver_options(self) -> ExternalReviewerConfig:
        if self.driver is not ExternalReviewDriver.CLAUDE_CODE and self.fast is not None:
            raise ValueError("fast is only supported by the claude-code driver")
        if self.driver is ExternalReviewDriver.CLAUDE_CODE and self.fast is True:
            raise ValueError("external Claude Code review must not enable fast mode")
        if self.driver is ExternalReviewDriver.CURSOR_CLI and self.effort is not None:
            raise ValueError(
                "cursor-cli model IDs encode effort; external reviewer effort must be omitted"
            )
        for fallback in self.fallbacks:
            if (
                self.driver is ExternalReviewDriver.ANTIGRAVITY
                and fallback.model == "claude-sonnet-4-6"
                and fallback.effort is not None
            ):
                raise ValueError("Antigravity claude-sonnet-4-6 fallback cannot set effort")
            if self.driver is ExternalReviewDriver.CURSOR_CLI and fallback.effort is not None:
                raise ValueError(
                    "cursor-cli fallback model IDs encode effort; fallback effort must be omitted"
                )
        return self


class ExternalSecondOpinionConfig(StrictModel):
    mode: str = "on-uncertainty"
    confidence_below: float = Field(default=0.9, ge=0, le=1)
    severities: list[Severity] = Field(default_factory=lambda: [Severity.BLOCKER, Severity.MAJOR])

    @field_validator("mode")
    @classmethod
    def require_supported_second_opinion_mode(cls, value: str) -> str:
        if value != "on-uncertainty":
            raise ValueError("second_opinion.mode must be on-uncertainty")
        return value


class ExternalReviewConfig(StrictModel):
    enabled: bool = True
    assignment: str = "least-used"
    assignment_since: str | None = None
    reviewers_per_batch: int = Field(default=1, ge=1)
    reviewers: list[ExternalReviewerConfig]
    second_opinion: ExternalSecondOpinionConfig = Field(default_factory=ExternalSecondOpinionConfig)
    domain_expertise: str | None = None

    @field_validator("domain_expertise")
    @classmethod
    def require_nonempty_domain_expertise(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("external_review.domain_expertise must not be empty")
        return normalized

    @field_validator("assignment_since")
    @classmethod
    def require_utc_assignment_since(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("external_review.assignment_since must be ISO 8601") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("external_review.assignment_since must include a UTC offset")
        return value

    @model_validator(mode="after")
    def validate_external_review_config(self) -> ExternalReviewConfig:
        if self.assignment != "least-used":
            raise ValueError("external_review.assignment must be least-used")
        if self.reviewers_per_batch != 1:
            raise ValueError("external_review.reviewers_per_batch must be 1")
        if not self.reviewers:
            raise ValueError("external_review.reviewers must contain at least one reviewer")
        ids = [reviewer.id for reviewer in self.reviewers]
        if len(ids) != len(set(ids)):
            raise ValueError("external reviewer IDs must be unique")
        return self


class RoleDispatch(StrictModel):
    """One role's dispatch policy. Either field may be unset: the host's default applies."""

    model: str | None = Field(
        default=None,
        description=(
            "Dispatch model for this role: the value passed to the host's task launcher "
            "(a host alias or a concrete id). Not the served model. Unset follows the "
            "host's default subagent model."
        ),
    )
    reasoning_effort: str | None = Field(
        default=None,
        description=(
            "Dispatched reasoning effort for this role, independent of every other role. "
            "Unset follows the host's default. Not applied on Claude Code, whose LitTrans "
            "agents fix their effort in their frontmatter."
        ),
    )


class ProjectConfig(StrictModel):
    schema_version: int = PROJECT_SCHEMA_VERSION
    project_id: str
    title: str
    source_path: str
    source_sha256: str
    source_pages: int
    profile: str
    record_root_relative: str | None = Field(
        default=None,
        pattern=r"^(?:\.|\.\.(?:/\.\.)*)$",
        description="Portable path from the project to its scaffold record root; absent in older projects.",
    )
    source_language: str = "en"
    target_language: str = "zh-CN"
    rights_status: str = "private-research-only"
    external_review: ExternalReviewConfig | None = None
    agent_models: dict[str, dict[str, RoleDispatch]] = Field(
        default_factory=host_model_defaults,
        validate_default=True,
        description=(
            "Per-host role dispatch policy: translate, transcribe, audit, asset-audit and "
            "source-review, each with its own model and reasoning_effort. Each model is the "
            "dispatch value handed to that host's task launcher, a host alias or a concrete id "
            "as the host defines; which model the host serves under it is the host's own "
            "configuration and is never verified here. An unset role, model or effort is "
            "supported and follows the host's own default subagent behaviour. Claude Code takes "
            "no per-dispatch effort: the LitTrans agents' frontmatter sets it."
        ),
    )
    status: ProjectStatus = ProjectStatus.INITIALIZED
    extractor_version: str = "2"
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="before")
    @classmethod
    def normalize_dispatch_policy(cls, payload: Any) -> Any:
        """Read `agent_models` in the nested or the legacy flat form, rejecting stray keys."""
        if isinstance(payload, dict) and "agent_models" in payload:
            payload = {**payload, "agent_models": normalize_agent_models(payload["agent_models"])}
        return payload

    def dispatch(self, host: str, role: str) -> RoleDispatch:
        """The configured policy for one role on one host; unset fields stay None."""
        return self.agent_models.get(host, {}).get(role, RoleDispatch())

    def source(self, project_root: Path) -> Path:
        path = Path(self.source_path)
        return path if path.is_absolute() else (project_root / path).resolve()


class AssetRef(StrictModel):
    kind: str
    path: str
    bbox: tuple[float, float, float, float]


class SourceFragment(StrictModel):
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float]


class TableData(StrictModel):
    rows: list[list[str]]
    header_rows: int = Field(default=1, ge=0)
    column_count: int = Field(ge=1)

    @field_validator("rows")
    @classmethod
    def require_rectangular_rows(cls, value: list[list[str]]) -> list[list[str]]:
        if not value or not value[0]:
            raise ValueError("table rows must not be empty")
        width = len(value[0])
        if any(len(row) != width for row in value):
            raise ValueError("table rows must all have the same number of columns")
        return value

    @model_validator(mode="after")
    def require_declared_width(self) -> TableData:
        if self.rows and len(self.rows[0]) != self.column_count:
            raise ValueError("column_count does not match table row width")
        if self.header_rows > len(self.rows):
            raise ValueError("header_rows cannot exceed the row count")
        return self


class FigureLabel(StrictModel):
    source: str
    target: str | None = None


class SourceUnit(StrictModel):
    schema_version: int = 3
    unit_id: str
    kind: UnitKind
    page: int
    bbox: tuple[float, float, float, float]
    source_text: str
    source_hash: str
    asset_content_hashes: dict[str, str] = Field(default_factory=dict)
    source_markdown: str | None = None
    parent_id: str | None = None
    sidebar_id: str | None = None
    sidebar_role: SidebarRole | None = None
    callout_kind: CalloutKind | None = None
    translatable: bool = True
    render_policy: RenderPolicy = RenderPolicy.INCLUDE
    protected_tokens: list[str] = Field(default_factory=list)
    asset_refs: list[AssetRef] = Field(default_factory=list)
    fragments: list[SourceFragment] = Field(default_factory=list)
    latex: str | None = None
    equation_number: str | None = None
    footnote_number: str | None = None
    footnote_refs: list[str] = Field(default_factory=list)
    math_status: SemanticStatus | None = None
    code_language: str | None = None
    table: TableData | None = None
    continues_from_previous: bool = False
    continued_to_next: bool = False
    figure_labels: list[FigureLabel] = Field(default_factory=list)
    visual_text_status: SemanticStatus | None = None
    verification_status: SemanticStatus = SemanticStatus.UNVERIFIED
    confidence: float = Field(ge=0, le=1)
    status: ProjectStatus = ProjectStatus.EXTRACTED

    @model_validator(mode="after")
    def require_omitted_units_to_be_nontranslatable(self) -> SourceUnit:
        if self.render_policy is RenderPolicy.OMIT and self.translatable:
            raise ValueError("omitted units cannot be translatable")
        if (self.sidebar_id is None) != (self.sidebar_role is None):
            raise ValueError("sidebar_id and sidebar_role must be set together")
        if self.sidebar_id is not None and not self.sidebar_id.strip():
            raise ValueError("sidebar_id must not be empty")
        if self.sidebar_role is SidebarRole.TITLE and self.kind is not UnitKind.HEADING:
            raise ValueError("a sidebar title must retain heading semantics")
        if self.sidebar_role is SidebarRole.BODY and self.kind not in {
            UnitKind.PARAGRAPH,
            UnitKind.LIST_ITEM,
            UnitKind.CODE,
            UnitKind.TABLE,
            UnitKind.FIGURE,
        }:
            raise ValueError("unsupported sidebar body unit kind")
        if self.callout_kind is not None and self.kind is not UnitKind.NOTE:
            raise ValueError("callout_kind is valid only for note units")
        return self


_MATH_REVIEW_REPRESENTATION_FIELDS = (
    "source_markdown",
    "latex",
    "equation_number",
    "math_status",
    "verification_status",
    "confidence",
)


def canonical_math_review_unit_guard_sha256(unit: SourceUnit) -> str:
    """Hash source/layout state that a math review is not allowed to change."""

    payload = unit.model_dump(
        mode="json",
        exclude={*_MATH_REVIEW_REPRESENTATION_FIELDS, "asset_refs"},
    )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def canonical_math_review_representation_sha256(unit: SourceUnit) -> str:
    """Hash the review-controlled representation/status state of a source unit."""

    serialized = unit.model_dump(mode="json")
    payload = {field: serialized[field] for field in _MATH_REVIEW_REPRESENTATION_FIELDS}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


class ReaderNote(StrictModel):
    text: str
    sources: list[str] = Field(default_factory=list)
    accessed_at: str | None = None

    @field_validator("sources")
    @classmethod
    def require_https_sources(cls, value: list[str]) -> list[str]:
        if any(not source.startswith("https://") for source in value):
            raise ValueError("reader-note sources must use https URLs")
        return value


class TermProposal(StrictModel):
    source: str
    target: str
    reason: str | None = None


class AssetTranslation(StrictModel):
    asset_id: str
    language_present: bool = True
    target_text: str = ""
    target_table: TableData | None = None
    figure_labels: list[FigureLabel] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def require_explained_language_omission(self) -> AssetTranslation:
        if not self.language_present and not self.notes.strip():
            raise ValueError("Explain why the original asset contains no translatable natural language")
        return self


class TranslationRecord(StrictModel):
    schema_version: int = 2
    unit_id: str
    target_text: str
    target_table: TableData | None = None
    figure_labels: list[FigureLabel] = Field(default_factory=list)
    source_hash: str
    image_evidence: dict[str, str] = Field(default_factory=dict)
    asset_translations: list[AssetTranslation] = Field(default_factory=list)
    revision: int = Field(default=1, ge=1)
    reader_note: ReaderNote | None = None
    term_proposals: list[TermProposal] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    status: ProjectStatus = ProjectStatus.DRAFT
    updated_at: str = Field(default_factory=utc_now)


class GlossaryTerm(StrictModel):
    source: str
    target: str
    status: str = "approved"
    scope: str = "document"
    forbidden: list[str] = Field(default_factory=list)
    definition: str | None = None


class ReviewIssue(StrictModel):
    schema_version: int = 1
    issue_id: str
    # Reviewer-supplied id retained when a packet import canonicalizes issue_id.
    source_issue_id: str | None = None
    batch_id: BatchId
    unit_id: str
    severity: Severity
    type: IssueType
    source_span: str | None = None
    target_span: str | None = None
    explanation: str
    suggested_revision: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    reviewer: str
    status: IssueStatus = IssueStatus.OPEN
    resolution: str | None = None
    resolved_at: str | None = None

    @model_validator(mode="after")
    def require_resolution_evidence(self) -> ReviewIssue:
        if self.status is not IssueStatus.OPEN:
            if self.resolution is None or not self.resolution.strip():
                raise ValueError("closed review issues require a non-empty resolution")
            if self.resolved_at is None or not self.resolved_at.strip():
                raise ValueError("closed review issues require resolved_at")
        return self


class ExternalReviewRun(StrictModel):
    schema_version: int = 2
    run_id: str
    batch_id: BatchId
    reviewer_id: str
    driver: ExternalReviewDriver
    role: str
    requested_model: str
    actual_model: str | None = None
    actual_model_label: str | None = None
    model_verified: bool = False
    cli_version: str | None = None
    effort: str | None = None
    fast_mode: str | None = None
    translation_fingerprint: str
    packet_sha256: str
    prompt_version: str
    scope: ReviewScope = ReviewScope.FULL
    base_run_id: str | None = None
    covered_unit_ids: list[str] = Field(default_factory=list)
    unit_fingerprints: dict[str, str] = Field(default_factory=dict)
    source_fingerprint: str | None = None
    structure_fingerprint: str | None = None
    context_fingerprint: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    usage: ReviewUsage | None = None
    cost_usd: float | None = Field(default=None, ge=0)
    prompt_delivery: PromptDelivery = PromptDelivery.FILE
    verdict: ExternalReviewVerdict
    summary: str
    issue_ids: list[str] = Field(default_factory=list)
    response_path: str | None = None
    attempts: int = Field(default=1, ge=1)
    failure_type: (
        Literal[
            "authentication",
            "network",
            "format",
            "model",
            "quota",
            "timeout",
            "provider",
            "unknown",
        ]
        | None
    ) = None
    fallback_of: str | None = None
    attempt_log_path: str | None = None
    success: bool = True
    reviewed_at: str = Field(default_factory=utc_now)

    @field_validator("role")
    @classmethod
    def require_supported_external_role(cls, value: str) -> str:
        if value not in {"primary", "second-opinion"}:
            raise ValueError("external review role must be primary or second-opinion")
        return value


class ReviewUsage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    provider_turns: int = Field(default=0, ge=0)


class MathCandidate(StrictModel):
    """One non-authoritative visual transcription candidate for a source unit."""

    schema_version: int = 1
    candidate_id: str
    unit_id: str
    page: int = Field(ge=1)
    source_pdf_sha256: Sha256Digest
    source_hash: Sha256Digest
    crop_path: str
    crop_sha256: Sha256Digest
    provider: str
    model: str
    prompt_version: str
    pass_index: int = Field(default=1, ge=1, le=2)
    classification: MathCandidateClassification
    latex: str | None = None
    source_markdown: str | None = None
    equation_number: str | None = None
    uncertainties: list[str] = Field(default_factory=list)
    needs_second_pass: bool = False
    request_sha256: Sha256Digest
    response_sha256: Sha256Digest
    raw_response_path: str
    usage: ReviewUsage = Field(default_factory=ReviewUsage)
    estimated_cost_usd: float = Field(default=0.0, ge=0)
    generated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def require_candidate_representation(self) -> MathCandidate:
        if self.classification is MathCandidateClassification.DISPLAY and not self.latex:
            raise ValueError("display math candidates require latex")
        if self.classification is MathCandidateClassification.INLINE and not self.source_markdown:
            raise ValueError("inline math candidates require source_markdown")
        return self


class MathReviewDecision(StrictModel):
    """A visual-review decision bound to immutable PDF and crop evidence."""

    schema_version: int = 1
    decision_id: str
    unit_id: str
    page: int = Field(ge=1)
    candidate_id: str | None = None
    packet_id: BatchId | None = None
    packet_sha256: Sha256Digest | None = None
    manifest_sha256: Sha256Digest | None = None
    review_crop_path: str | None = None
    disposition: MathReviewDisposition
    source_pdf_sha256: Sha256Digest
    source_hash: Sha256Digest
    crop_sha256: Sha256Digest
    page_image_sha256: Sha256Digest
    reviewed_against_pdf: bool
    reviewer_id: str
    reviewer_model: str
    reviewer_effort: str
    reviewer_task: str | None = None
    reason: str
    final_latex: str | None = None
    final_source_markdown: str | None = None
    equation_number: str | None = None
    structural_issues: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    reviewed_at: str = Field(default_factory=utc_now)

    @field_validator(
        "decision_id",
        "unit_id",
        "source_pdf_sha256",
        "source_hash",
        "crop_sha256",
        "page_image_sha256",
        "reviewer_id",
        "reviewer_model",
        "reviewer_effort",
        "reason",
    )
    @classmethod
    def require_nonempty_math_review_values(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("math review evidence values must not be empty")
        return value

    @model_validator(mode="after")
    def require_review_evidence(self) -> MathReviewDecision:
        packet_binding = (
            self.packet_id,
            self.packet_sha256,
            self.manifest_sha256,
            self.review_crop_path,
        )
        if any(value is not None for value in packet_binding) and not all(
            value is not None for value in packet_binding
        ):
            raise ValueError(
                "packet-bound math reviews require packet_id, packet_sha256, "
                "manifest_sha256, and review_crop_path"
            )
        if self.review_crop_path is not None and not self.review_crop_path.strip():
            raise ValueError("review_crop_path must not be empty")
        if self.disposition is MathReviewDisposition.ACCEPTED and not self.candidate_id:
            raise ValueError("accepted math reviews require candidate_id")
        if self.disposition in {
            MathReviewDisposition.CORRECTED,
            MathReviewDisposition.MANUAL,
        } and not (self.final_latex or self.final_source_markdown):
            raise ValueError("corrected or manual math reviews require a final representation")
        return self


class MathStructuralFinalValues(StrictModel):
    """Only source-layout values a structural sidecar is allowed to propose.

    In particular, this intentionally has no selector, insertion, confidence,
    translatability, render policy, or verification/status fields.
    """

    kind: UnitKind | None = None
    source_text: str | None = None
    source_markdown: str | None = None
    latex: str | None = None
    equation_number: str | None = None
    set_bbox: tuple[float, float, float, float] | None = None
    parent_id: str | None = None
    continues_from_previous: bool | None = None
    continued_to_next: bool | None = None


class MathStructuralOverrideDecision(StrictModel):
    """A packet-bound structural proposal; never a verification grant."""

    schema_version: Literal[5]
    packet_id: BatchId
    packet_payload_sha256: Sha256Digest
    decision_id: BatchId
    unit_id: BatchId
    page: int = Field(ge=1)
    source_pdf_sha256: Sha256Digest
    source_hash: Sha256Digest
    page_image_sha256: Sha256Digest
    action: MathStructuralAction
    target_unit_ids: list[BatchId]
    fields: list[MathStructuralField]
    final_values: MathStructuralFinalValues
    reviewed_against_pdf: bool
    reviewer_id: str
    reviewer_model: str
    reviewer_effort: str
    reviewer_task: str
    reason: str
    reviewed_at: str
    canonical_override_sha256: Sha256Digest

    @field_validator(
        "reviewer_id",
        "reviewer_model",
        "reviewer_effort",
        "reviewer_task",
        "reason",
        "reviewed_at",
    )
    @classmethod
    def require_nonempty_structural_review_values(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("structural review evidence values must not be empty")
        return value

    @model_validator(mode="after")
    def require_closed_structural_change(self) -> MathStructuralOverrideDecision:
        if len(self.target_unit_ids) != len(set(self.target_unit_ids)):
            raise ValueError("target_unit_ids must be unique")
        if self.unit_id in self.target_unit_ids:
            raise ValueError("target_unit_ids must not contain unit_id")
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("structural fields must be unique")
        supplied_values = set(self.final_values.model_fields_set)
        if supplied_values != set(self.fields):
            raise ValueError("fields must exactly match explicitly supplied final_values")
        if self.action is MathStructuralAction.IGNORE:
            if self.target_unit_ids or self.fields:
                raise ValueError("ignore structural decisions cannot target or update other fields")
        elif self.action is MathStructuralAction.MERGE:
            if len(self.target_unit_ids) != 1:
                raise ValueError("merge structural decisions require exactly one target unit")
        elif self.action is MathStructuralAction.SPLIT:
            if not self.target_unit_ids or not self.fields:
                raise ValueError("split structural decisions require targets and final fields")
        elif self.action is MathStructuralAction.RECLASSIFY:
            if self.target_unit_ids:
                raise ValueError("reclassify structural decisions cannot target other units")
            if "kind" not in self.fields:
                raise ValueError("reclassify structural decisions require a final kind")
        elif self.action is MathStructuralAction.REORDER:
            reorder_fields = {
                "parent_id",
                "continues_from_previous",
                "continued_to_next",
            }
            if not self.fields or not set(self.fields).issubset(reorder_fields):
                raise ValueError("reorder may only change parent/continuation fields")
        expected = canonical_math_structural_override_sha256(self)
        if self.canonical_override_sha256 != expected:
            raise ValueError("canonical_override_sha256 does not match the override payload")
        return self


def canonical_math_structural_override_sha256(
    decision: MathStructuralOverrideDecision | dict[str, Any],
) -> str:
    """Hash a structural decision canonically, excluding its hash field."""

    if isinstance(decision, BaseModel):
        payload = decision.model_dump(
            mode="json",
            exclude={"canonical_override_sha256"},
            exclude_unset=True,
        )
    else:
        payload = {
            key: value for key, value in decision.items() if key != "canonical_override_sha256"
        }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


class ExternalReviewAttempt(StrictModel):
    """One provider invocation attempt, including targeted format-repair attempts."""

    schema_version: int = 1
    run_id: str
    batch_id: BatchId
    attempt: int = Field(ge=1)
    reviewer_id: str
    driver: ExternalReviewDriver
    requested_model: str
    actual_model: str | None = None
    effort: str | None = None
    prompt_delivery: PromptDelivery
    duration_seconds: float = Field(ge=0)
    success: bool
    failure_type: (
        Literal[
            "authentication",
            "network",
            "format",
            "model",
            "quota",
            "timeout",
            "provider",
            "unknown",
        ]
        | None
    ) = None
    quota_pool: Literal["cursor-first-party", "cursor-third-party"] | None = None
    error: str | None = None
    targeted_repair_scheduled: bool = False
    usage: ReviewUsage = Field(default_factory=ReviewUsage)
    cost_usd: float | None = Field(default=None, ge=0)
    raw_response_path: str
    recorded_at: str = Field(default_factory=utc_now)


class PageVerificationReceipt(StrictModel):
    schema_version: int = 1
    page: int = Field(ge=1)
    source_sha256: str
    unit_fingerprint: str
    asset_fingerprint: str
    validator_version: str
    receipt_key: str
    passed: bool
    token_coverage: float = Field(ge=0, le=1)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    checked_at: str = Field(default_factory=utc_now)


class AuditRun(StrictModel):
    schema_version: int = 1
    run_id: str
    batch_ids: list[BatchId]
    reviewer: str
    lens: str
    scope: ReviewScope = ReviewScope.FULL
    base_run_id: str | None = None
    packet_id: str | None = None
    unit_fingerprints: dict[str, str]
    context_fingerprint: str | None = None
    # Brief, style guide and relevant approved/reference term hash alone, so staleness
    # can tell a context edit apart from a changed dependency unit.
    shared_context_fingerprint: str | None = None
    # The same context by part ({part: {sha256, lines}}), so a stale run can name which
    # whole-file context grew and by how much.
    shared_context_parts: dict[str, dict[str, Any]] | None = None
    context_unit_ids: list[str] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)
    reviewed_at: str = Field(default_factory=utc_now)

    @field_validator("lens")
    @classmethod
    def require_supported_audit_lens(cls, value: str) -> str:
        if value not in {"fidelity", "technical", "chinese-style"}:
            raise ValueError("unsupported audit lens")
        return value


# Stages `workflow packet --stage` accepts; source-review packets are review material
# without a manifest of their own.
WORKFLOW_PACKET_STAGES = ("source-review", "translate", "revise", "audit", "transcribe", "asset-audit")
WORKFLOW_MANIFEST_STAGES = frozenset(WORKFLOW_PACKET_STAGES) - {"source-review"}
# Why an audit run no longer counts, as `audit_coverage` reports it.
AUDIT_STALE_REASONS = (
    "context-changed",
    "dependency-changed",
    "unit-changed",
    "invalidated",
    "closure-incomplete",
    "context-units-removed",
)


class WorkflowPacketManifest(StrictModel):
    schema_version: int = 2
    packet_id: BatchId
    stage: str
    batch_ids: list[BatchId] = Field(min_length=1, max_length=WAVE_BATCH_SET_MAX)
    lens: str | None = None
    host: str | None = None
    model: str | None = Field(
        default=None,
        description=(
            "Dispatch model for the stage from agent_models.<host>: the value passed to the "
            "host's task launcher (alias or concrete id, host-specific), echoed by submissions. "
            "Not the served model."
        ),
    )
    reasoning_effort: str | None = Field(
        default=None,
        description="Dispatched reasoning effort from agent_models.<host>, echoed by submissions.",
    )
    unit_ids: list[str]
    unit_fingerprints: dict[str, str]
    # v2 binds evidence to each batch's own coverage and dependency closure.
    # Defaults preserve readability of v1 manifests during migration.
    batch_unit_ids: dict[BatchId, list[str]] = Field(default_factory=dict)
    batch_context_unit_ids: dict[BatchId, list[str]] = Field(default_factory=dict)
    batch_context_fingerprints: dict[BatchId, str] = Field(default_factory=dict)
    storage_root: str = "packets"
    files: dict[str, str]
    file_sha256: dict[str, str] = Field(default_factory=dict)
    total_bytes: int = Field(ge=0)
    created_at: str = Field(default_factory=utc_now)

    @field_validator("stage")
    @classmethod
    def require_supported_packet_stage(cls, value: str) -> str:
        if value not in WORKFLOW_MANIFEST_STAGES:
            raise ValueError(
                "workflow packet stage must be translate, revise, transcribe, asset-audit or audit"
            )
        return value

    @model_validator(mode="after")
    def require_audit_lens(self) -> WorkflowPacketManifest:
        if self.stage == "audit" and self.lens not in {
            "fidelity",
            "technical",
            "chinese-style",
        }:
            raise ValueError("audit packets require one supported lens")
        if self.stage in {"translate", "revise"} and self.lens is not None:
            raise ValueError("translation packets must not set a lens")
        return self


class BatchManifest(StrictModel):
    schema_version: int = 1
    batch_id: BatchId
    project_id: str
    pages: list[int]
    unit_ids: list[str]
    translatable_unit_ids: list[str]
    read_only_unit_ids: list[str] = Field(default_factory=list)
    frozen_scope: bool = False
    source_words: int
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_read_only_scope(self) -> BatchManifest:
        if (len(set(self.read_only_unit_ids)) != len(self.read_only_unit_ids)
                or not set(self.read_only_unit_ids) <= set(self.unit_ids)
                or set(self.read_only_unit_ids) & set(self.translatable_unit_ids)):
            raise ValueError("Read-only batch units must be unique context units outside translation scope")
        return self


class QAItem(StrictModel):
    code: str
    severity: str
    message: str
    unit_id: str | None = None


class QAReport(StrictModel):
    schema_version: int = 2
    batch_id: BatchId
    passed: bool
    translation_fingerprint: str
    qa_context_fingerprint: str | None = None
    errors: list[QAItem] = Field(default_factory=list)
    warnings: list[QAItem] = Field(default_factory=list)
    checked_at: str = Field(default_factory=utc_now)


class ExtractionIssue(StrictModel):
    issue_id: str | None = None
    page: int
    severity: Severity
    code: str
    message: str
    unit_id: str | None = None
    status: IssueStatus = IssueStatus.OPEN
    details: dict[str, Any] = Field(default_factory=dict)
