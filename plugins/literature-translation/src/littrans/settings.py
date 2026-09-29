"""Versioned project policy. Presets are expanded once, never runtime inheritance."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from littrans.hosts import COORDINATION_HOSTS, WAVE_LIMITS, host_model_defaults
from littrans.models import ExternalReviewerConfig, StrictModel

ROLES = ("translate", "revise", "transcribe", "source-review", "asset-audit", "audit",
         "external-recheck", "scout", "terminology")
LENSES = ("fidelity", "technical", "chinese-style")


class SettingsModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProjectManifest(SettingsModel):
    schema_version: Literal[7]
    project_id: str
    source_path: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_pages: int = Field(ge=1)
    record_root_relative: str | None = Field(pattern=r"^(?:\.|\.\.(?:/\.\.)*)$")
    extractor_version: str
    created_at: str


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


class DispatchOverride(SettingsModel):
    model: str | None = None
    reasoning_effort: str | None = None

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        for value in (self.model, self.reasoning_effort):
            if value is not None and not value.strip():
                raise ValueError("Dispatch values must be nonempty or null")
        return self


class HostPolicy(SettingsModel):
    defaults: DispatchOverride
    roles: dict[str, DispatchOverride]
    audit_lenses: dict[str, DispatchOverride]
    wave_size: int = Field(ge=1)

    @model_validator(mode="after")
    def keys(self) -> Self:
        if set(self.roles) - set(ROLES) or set(self.audit_lenses) - set(LENSES):
            raise ValueError("Unknown dispatch role or audit lens")
        return self

    def resolve(self, role: str, lens: str | None = None) -> dict[str, Any]:
        if role not in ROLES or (lens is not None and (role != "audit" or lens not in LENSES)):
            raise ValueError("Unknown role/lens")
        result = self.defaults.model_dump()
        inherited = {"revise": "translate", "external-recheck": "audit"}.get(role)
        for key in ([inherited] if inherited else []) + [role]:
            if key in self.roles:
                result.update(self.roles[key].model_dump(exclude_unset=True))
        if lens in self.audit_lenses:
            result.update(self.audit_lenses[lens].model_dump(exclude_unset=True))
        return result


class DocumentSettings(SettingsModel):
    title: str = Field(min_length=1)
    source_language: str = Field(min_length=1)
    target_language: str = Field(min_length=1)
    rights_status: str = Field(min_length=1)


class BatchSettings(SettingsModel):
    max_source_words: int = Field(ge=100)
    soft_max_assets: int = Field(ge=1)
    preserve_heading_boundaries: bool


class CodeSettings(SettingsModel):
    translate_comments: bool
    translate_string_literals: bool
    detect_language: bool


class EquationSettings(SettingsModel):
    inline: Literal["original", "reviewed-transcription"]
    display: Literal["original", "reviewed-transcription"]


class FigureSettings(SettingsModel):
    translate_caption: bool
    internal_labels: Literal["preserve", "explain-below"]


class TableSettings(SettingsModel):
    translation: Literal["cells", "cells-or-region"]
    presentation: Literal["original", "reviewed-transcription"]
    image_fallback_in_final: bool

    @model_validator(mode="after")
    def fallback(self) -> Self:
        if self.presentation == "original" and not self.image_fallback_in_final:
            raise ValueError("Disabling table image fallback requires reviewed-transcription presentation")
        return self


class ReaderNoteSettings(SettingsModel):
    allow_modernization: bool
    require_primary_https_sources: bool


class TranslationSettings(SettingsModel):
    code: CodeSettings
    equations: EquationSettings
    figures: FigureSettings
    tables: TableSettings
    reader_notes: ReaderNoteSettings
    memory_minimum_status: Literal["machine-reviewed", "external-reviewed", "human-approved"]
    prose_punctuation: Literal["chinese", "preserve"]


class VerificationSettings(SettingsModel):
    block_unfinished_transcription: bool


class ReviewerSettings(SettingsModel):
    driver: Literal["claude-code", "antigravity", "cursor-cli", "codex-cli", "opencode-cli"]
    model: str
    model_identity: str | None
    effort: str | None

    @model_validator(mode="after")
    def driver_options(self) -> Self:
        ExternalReviewerConfig(id="validation", command="validation", **self.model_dump())
        return self


class RecheckSettings(SettingsModel):
    confidence_below: float = Field(ge=0, le=1)
    severities: list[Literal["blocker", "major", "minor", "suggestion"]]

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(self.severities) != len(set(self.severities)):
            raise ValueError("Recheck severities must be unique")
        return self


class ExternalSettings(SettingsModel):
    enabled: bool
    reviewers: dict[str, ReviewerSettings]
    primary: str | None
    fallbacks: list[str]
    recheck: RecheckSettings
    domain_expertise: str | None
    timeout_seconds: int = Field(ge=1, le=86400)

    @model_validator(mode="after")
    def references(self) -> Self:
        ids = ([self.primary] if self.primary else []) + self.fallbacks
        if any(not key or "." in key or not key.strip() for key in self.reviewers):
            raise ValueError("Reviewer IDs must be nonempty and contain no dots")
        if len(ids) != len(set(ids)) or set(ids) - self.reviewers.keys():
            raise ValueError("Reviewer references must exist and be unique")
        if self.enabled and not self.primary:
            raise ValueError("Enabled external review requires a primary reviewer")
        if self.domain_expertise is not None and not self.domain_expertise.strip():
            raise ValueError("domain_expertise must be nonempty or null")
        return self


class PresetOrigin(SettingsModel):
    name: str
    version: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ProjectSettings(SettingsModel):
    schema_version: Literal[1]
    preset: PresetOrigin
    document: DocumentSettings
    batch: BatchSettings
    outline_source: Literal["pdf-bookmarks", "font-and-numbering", "auto"]
    agents: dict[str, HostPolicy]
    translation: TranslationSettings
    verification: VerificationSettings
    external_review: ExternalSettings

    @model_validator(mode="after")
    def hosts(self) -> Self:
        if set(self.agents) != set(COORDINATION_HOSTS):
            raise ValueError("agents must explicitly contain every supported host")
        for host in COORDINATION_HOSTS:
            policy = self.agents[host]
            if policy.wave_size > WAVE_LIMITS[host].maximum:
                raise ValueError(f"agents.{host}.wave_size exceeds host maximum")
        return self

    def payload(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        # Omitted override fields inherit; explicit null resets to host behaviour.
        for host, policy in self.agents.items():
            for group in ("roles", "audit_lenses"):
                value["agents"][host][group] = {
                    key: item.model_dump(exclude_unset=True)
                    for key, item in getattr(policy, group).items()
                }
        return value


# Public field ownership is emitted alongside JSON Schema and used by docs/tests.
FIELD_GROUPS = {
    "schema_version": ("format", "configuration loader", "immutable format version"),
    "preset": ("provenance", "initialization/reset", "preset source; no runtime inheritance"),
    "document": ("document", "packets/rendering/provenance", "language changes invalidate translation evidence"),
    "batch": ("batch", "batch creation", "future batches only"),
    "outline_source": ("source", "source preparation/review", "source receipts become stale"),
    "agents": ("dispatch", "task creation/native agent generation", "new tasks only"),
    "translation": ("translation", "submission/QA/audit/rendering", "dependent QA/audits or presentation"),
    "verification": ("verification", "approval/final rendering", "delivery requirements"),
    "external_review": ("external", "external CLI/recheck gates", "model/context changes stale external evidence"),
}


def settings_schema(local: bool = False) -> dict[str, Any]:
    model = LocalSettings if local else ProjectSettings
    schema = model.model_json_schema()
    fields = {}

    def visit(node: dict[str, Any], path: str, metadata: tuple[str, str, str]) -> None:
        if "$ref" in node:
            node = schema["$defs"][node["$ref"].rsplit("/", 1)[-1]]
        if "properties" in node:
            for key, child in node["properties"].items():
                visit(child, f"{path}.{key}" if path else key, metadata)
        elif isinstance(node.get("additionalProperties"), dict):
            visit(node["additionalProperties"], path + ".*", metadata)
        else:
            domain, consumer, effect = metadata
            if path.startswith(("translation.equations", "translation.tables.presentation", "translation.tables.image_fallback")):
                domain, consumer, effect = "presentation", "asset selection/approval/rendering", "output plus required transcription gate"
            elif path == "translation.code.detect_language":
                domain, consumer, effect = "source", "source preparation", "source receipts become stale"
            elif path.startswith("external_review.recheck"):
                domain, consumer, effect = "recheck", "external status/adjudication", "recompute required rechecks from saved findings"
            elif path == "external_review.timeout_seconds":
                domain, consumer, effect = "execution", "external subprocess", "future invocations only"
            fields[path] = {**node, "x-domain": domain, "x-consumer": consumer,
                            "x-change-effect": effect, "x-editable": path != "schema_version",
                            "description": node.get("description", f"Consumed by {consumer}; {effect}.")}

    for key, value in schema["properties"].items():
        metadata = ("local", "source/provider binding", "machine binding only") if local else FIELD_GROUPS[key]
        domain, consumer, effect = metadata
        value.update({"x-domain": domain, "x-consumer": consumer, "x-change-effect": effect,
                      "x-editable": key != "schema_version"})
        value.setdefault("description", f"Consumed by {consumer}; {effect}.")
        visit(value, key, metadata)
    schema["x-fields"] = fields
    return schema


class LocalSettings(SettingsModel):
    schema_version: Literal[1] = 1
    source_path: str | None = None
    commands: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if any(not value.strip() for value in self.commands.values()):
            raise ValueError("Executable bindings must not be empty")
        if self.source_path is not None and not self.source_path.strip():
            raise ValueError("source_path must not be empty")
        return self


def preset(name: str, title: str, source_language: str = "en",
           target_language: str = "zh-CN") -> ProjectSettings:
    if name not in ("technical-book", "research-paper"):
        raise ValueError("Unknown preset; choose technical-book or research-paper")
    defaults = host_model_defaults()
    value = {
        "schema_version": 1,
        "document": {"title": title, "source_language": source_language,
                     "target_language": target_language, "rights_status": "private-research-only"},
        "batch": {"max_source_words": 900, "soft_max_assets": 60, "preserve_heading_boundaries": True},
        "outline_source": "auto" if name == "technical-book" else "font-and-numbering",
        "agents": {host: {"defaults": {"model": None, "reasoning_effort": None},
                          "roles": defaults.get(host, {}), "audit_lenses": {},
                          "wave_size": WAVE_LIMITS[host].default} for host in COORDINATION_HOSTS},
        "translation": {
            "code": {"translate_comments": False, "translate_string_literals": False, "detect_language": True},
            "equations": {"inline": "original", "display": "original"},
            "figures": {"translate_caption": True, "internal_labels": "explain-below"},
            "tables": {"translation": "cells-or-region", "presentation": "original", "image_fallback_in_final": True},
            "reader_notes": {"allow_modernization": name == "technical-book", "require_primary_https_sources": True},
            "memory_minimum_status": "machine-reviewed",
            "prose_punctuation": "chinese" if target_language.lower().startswith("zh") else "preserve",
        },
        "verification": {"block_unfinished_transcription": False},
        "external_review": {"enabled": False, "reviewers": {}, "primary": None, "fallbacks": [],
                            "recheck": {"confidence_below": 0.9, "severities": ["blocker", "major"]},
                            "domain_expertise": None, "timeout_seconds": 330},
    }
    value["preset"] = {"name": name, "version": "1", "sha256": digest(value)}
    return ProjectSettings.model_validate(value)
