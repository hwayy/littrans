"""Public submission contracts for contextual transcription and independent review."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from littrans.models import StrictModel


class AssetCandidateInput(StrictModel):
    asset_id: str
    format: Literal["latex", "table", "code", "text"] = "latex"
    content: str | dict[str, Any] = ""
    status: Literal["candidate", "unresolved"] = "candidate"
    notes: str = ""
    semantic_uncertainty: str = ""


class AssetSubmission(StrictModel):
    packet_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    author_task_id: str = Field(min_length=1)
    # `model`/`reasoning_effort` echo the packet's dispatch policy: the value the
    # coordinator handed to the host's task launcher (an alias such as `sonnet`
    # or a concrete id, host-specific). The model a host actually served under
    # that value is a separate, unverified observation.
    model: str | None = Field(
        default=None,
        description=(
            "The packet's dispatch model, echoed verbatim: the value passed to the host's task "
            "launcher (a host alias or a concrete id). Not the served model. Absent only when "
            "the packet records no model and the task ran on the host's default."
        ),
    )
    reasoning_effort: str | None = Field(
        default=None,
        description="The packet's dispatched reasoning effort, echoed verbatim.",
    )
    served_model_label: str | None = Field(
        default=None,
        description=(
            "The model the host environment reported to the task under the dispatch value, "
            "recorded verbatim and unverified; never used for gating."
        ),
    )
    image_evidence: dict[str, str]
    candidates: list[AssetCandidateInput] = Field(min_length=1)
    usage: dict[str, Any] | None = None


class AssetReviewItem(StrictModel):
    asset_id: str
    candidate_sha256: str
    verdict: Literal["accept", "reject", "unresolved"]
    visual_checked: bool
    render_checked: bool
    notes: str = ""
    semantic_uncertainty: str = ""


class AssetReviewSubmission(StrictModel):
    packet_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer_task_id: str = Field(min_length=1)
    image_evidence: dict[str, str]
    render_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    render_artifact_sha256: str
    decisions: list[AssetReviewItem] = Field(min_length=1)
