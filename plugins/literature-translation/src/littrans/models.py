from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class ExternalReviewVerdict(StrEnum):
    ACCEPTED = "accepted"
    CHANGES_REQUESTED = "changes-requested"
    INCONCLUSIVE = "inconclusive"


class ExternalReviewFallback(StrictModel):
    model: str
    effort: str | None = None


class ExternalReviewerConfig(StrictModel):
    id: str
    driver: ExternalReviewDriver
    command: str
    model: str
    effort: str | None = None
    fast: bool | None = None
    fallbacks: list[ExternalReviewFallback] = Field(default_factory=list)

    @field_validator("id", "command", "model")
    @classmethod
    def require_nonempty_external_reviewer_values(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("external reviewer values must not be empty")
        return value

    @model_validator(mode="after")
    def validate_driver_options(self) -> ExternalReviewerConfig:
        if self.driver is ExternalReviewDriver.ANTIGRAVITY and self.fast is not None:
            raise ValueError("fast is only supported by the claude-code driver")
        if self.driver is ExternalReviewDriver.CLAUDE_CODE and self.fast is True:
            raise ValueError("external Claude Code review must not enable fast mode")
        for fallback in self.fallbacks:
            if (
                self.driver is ExternalReviewDriver.ANTIGRAVITY
                and fallback.model == "claude-sonnet-4-6"
                and fallback.effort is not None
            ):
                raise ValueError("Antigravity claude-sonnet-4-6 fallback cannot set effort")
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
    reviewers_per_batch: int = Field(default=1, ge=1)
    reviewers: list[ExternalReviewerConfig]
    second_opinion: ExternalSecondOpinionConfig = Field(default_factory=ExternalSecondOpinionConfig)

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


class ProjectConfig(StrictModel):
    schema_version: int = 3
    project_id: str
    title: str
    source_path: str
    source_sha256: str
    source_pages: int
    profile: str
    source_language: str = "en"
    target_language: str = "zh-CN"
    rights_status: str = "private-research-only"
    external_review: ExternalReviewConfig | None = None
    status: ProjectStatus = ProjectStatus.INITIALIZED
    extractor_version: str = "2"
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

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
    schema_version: int = 2
    unit_id: str
    kind: UnitKind
    page: int
    bbox: tuple[float, float, float, float]
    source_text: str
    source_hash: str
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


class TranslationRecord(StrictModel):
    schema_version: int = 2
    unit_id: str
    target_text: str
    target_table: TableData | None = None
    figure_labels: list[FigureLabel] = Field(default_factory=list)
    source_hash: str
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
    batch_id: str
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


class ExternalReviewRun(StrictModel):
    schema_version: int = 1
    run_id: str
    batch_id: str
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
    verdict: ExternalReviewVerdict
    summary: str
    issue_ids: list[str] = Field(default_factory=list)
    response_path: str | None = None
    attempts: int = Field(default=1, ge=1)
    success: bool = True
    reviewed_at: str = Field(default_factory=utc_now)

    @field_validator("role")
    @classmethod
    def require_supported_external_role(cls, value: str) -> str:
        if value not in {"primary", "second-opinion"}:
            raise ValueError("external review role must be primary or second-opinion")
        return value


class BatchManifest(StrictModel):
    schema_version: int = 1
    batch_id: str
    project_id: str
    pages: list[int]
    unit_ids: list[str]
    translatable_unit_ids: list[str]
    source_words: int
    created_at: str = Field(default_factory=utc_now)


class QAItem(StrictModel):
    code: str
    severity: str
    message: str
    unit_id: str | None = None


class QAReport(StrictModel):
    schema_version: int = 1
    batch_id: str
    passed: bool
    translation_fingerprint: str
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
