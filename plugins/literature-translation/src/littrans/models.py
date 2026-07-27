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


class ProjectConfig(StrictModel):
    schema_version: int = 2
    project_id: str
    title: str
    source_path: str
    source_sha256: str
    source_pages: int
    profile: str
    source_language: str = "en"
    target_language: str = "zh-CN"
    rights_status: str = "private-research-only"
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
