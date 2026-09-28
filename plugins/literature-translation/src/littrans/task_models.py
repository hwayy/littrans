"""Envelope schema; domain packet/result schemas remain independently validated."""
from typing import Any, Literal

from pydantic import Field

from littrans.models import StrictModel


class TaskEnvelope(StrictModel):
    schema_version: Literal[1] = 1
    task_id: str = Field(pattern=r"^task-[a-f0-9]{24}$")
    stage: Literal["source-review", "translate", "revise", "audit", "transcribe",
                   "asset-audit", "scout", "terminology"]
    role: str
    lens: str | None
    objective: str | None
    batch_ids: list[str]
    pages: str | None
    asset_ids: list[str]
    packet: dict[str, Any]
    domain_manifest: str | None
    inputs: dict[str, str]
    context: dict[str, Any]
    instructions: dict[str, str]
    dispatch: dict[str, Any]
    source_sha256: str
    source_bindings: dict[str, str] = Field(default_factory=dict)
    batch_binding: dict[str, Any] = Field(default_factory=dict)
