"""Local review and import of visual mathematics candidates.

The vision pass is deliberately kept separate from the source-unit ledger.  A
candidate is only useful as evidence; the source ledger is changed later by
the normal ``source apply-overrides`` command.  This module therefore writes
only a local HTML report, review receipts, and proposed layout overrides.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pymupdf as fitz
import yaml
from latex2mathml.converter import convert as latex_to_mathml

from littrans.extractor import parse_page_spec
from littrans.math_packets import (
    MANUAL_REVIEW_CROP_PADDING_PT,
    MANUAL_REVIEW_CROP_SCALE,
    _manual_review_crop,
    _needs_math_review,
    _render_full_page,
)
from littrans.models import (
    MathCandidate,
    MathCandidateClassification,
    MathReviewDecision,
    MathReviewDisposition,
    MathStructuralAction,
    MathStructuralOverrideDecision,
    MathStructuralReviewSidecar,
    SemanticStatus,
    SourceUnit,
    UnitKind,
    canonical_math_review_representation_sha256,
    canonical_math_review_unit_guard_sha256,
    canonical_math_structural_override_sha256,
)
from littrans.storage import (
    atomic_write_bytes,
    atomic_write_text,
    load_project,
    project_write_lock,
    read_jsonl,
    restore_files,
    sha256_file,
    sha256_text,
    snapshot_files,
    write_yaml,
)

REPORT_PATH = Path("derived") / "math-review-report.html"
CANDIDATES_PATH = Path("evidence") / "math" / "candidates.jsonl"
REVIEWS_PATH = Path("evidence") / "math" / "reviews.jsonl"
STRUCTURAL_REVIEWS_PATH = Path("evidence") / "math" / "structural-reviews.jsonl"
LAYOUT_PATH = Path("overrides") / "layout.yaml"
PAGE_IMAGE_DIR = Path("derived") / "verification"

_INLINE_MATH_RE = re.compile(r"(?<!\\)\$(?!\$)(.+?)(?<!\\)\$(?!\$)", re.S)
_GENERIC_REASONS = {
    "ok",
    "yes",
    "done",
    "approved",
    "accepted",
    "looks good",
    "match",
    "correct",
}


def _value(value: Any) -> str:
    """Return an enum's value without requiring callers to know its type."""

    return str(getattr(value, "value", value))


def _read_candidates(root: Path) -> list[MathCandidate]:
    path = root / CANDIDATES_PATH
    if not path.exists():
        return []
    records: list[MathCandidate] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(MathCandidate.model_validate_json(line))
        except Exception as exc:  # pydantic supplies the useful field path
            raise ValueError(f"Invalid math candidate at {path}:{line_number}: {exc}") from exc
    return records


def _read_decisions(path: Path) -> list[MathReviewDecision]:
    """Read either the documented JSONL format or a convenient JSON array."""

    if not path.is_file():
        raise FileNotFoundError(path)
    raw = path.read_text(encoding="utf-8")
    records: list[Any]
    if path.suffix.lower() == ".json":
        payload = json.loads(raw)
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict):
            records = [payload]
        else:
            raise ValueError(f"Math review input must contain an object or array: {path}")
    else:
        records = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
    decisions: list[MathReviewDecision] = []
    for index, record in enumerate(records, 1):
        try:
            decisions.append(MathReviewDecision.model_validate(record))
        except Exception as exc:
            raise ValueError(f"Invalid math review decision #{index} in {path}: {exc}") from exc
    if not decisions:
        raise ValueError(f"Math review input contains no decisions: {path}")
    decision_ids = [decision.decision_id for decision in decisions]
    if len(set(decision_ids)) != len(decision_ids):
        raise ValueError(f"Math review input contains duplicate decision_id values: {path}")
    return decisions


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _explicit_packet_unit_ids(
    payload: Mapping[str, Any], unit_sequence: list[Any], label: str
) -> list[str]:
    """Validate the optional, hash-bound exception list in stable packet order."""

    if "explicit_unit_ids" not in payload:
        return []
    values = payload.get("explicit_unit_ids")
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(value, str) or not value for value in values)
        or len(values) != len(set(values))
    ):
        raise ValueError(f"{label} explicit unit sequence is stale or invalid")
    requested = set(values)
    if values != [value for value in unit_sequence if value in requested]:
        raise ValueError(f"{label} explicit unit sequence is stale or invalid")
    return values


def _read_structural_sidecar(path: Path) -> MathStructuralReviewSidecar:
    """Read a strict JSON/YAML sidecar without accepting a path from its payload."""

    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        if path.suffix.casefold() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix.casefold() in {".yaml", ".yml"}:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            raise ValueError("Structural review sidecar must be JSON or YAML")
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Invalid structural review sidecar {path}: {exc}") from exc
    try:
        return MathStructuralReviewSidecar.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"Invalid structural review sidecar {path}: {exc}") from exc


def _packet_manifest_candidates(root: Path, packet_id: str) -> list[Path]:
    """Return only fixed-root manifests; packet_id itself is schema-constrained."""

    candidates = [
        root / "packets" / "math-pilot" / packet_id / "packet" / "manifest.json",
        root
        / ".littrans"
        / "work"
        / "math-review-packets"
        / packet_id
        / "packet"
        / "manifest.json",
    ]
    # Pilot packet roots may be named by a run.  Discovering below the fixed
    # project-owned packets root preserves compatibility without trusting any
    # sidecar-provided path.
    candidates.extend((root / "packets").glob(f"**/{packet_id}/packet/manifest.json"))
    unique: dict[Path, Path] = {}
    for candidate in candidates:
        if not candidate.is_file():
            continue
        safe = _safe_project_file(root, str(candidate), "packet manifest path")
        unique[safe] = safe
    return sorted(unique.values(), key=lambda item: item.as_posix())


def _safe_packet_file(packet_root: Path, value: str, label: str) -> Path:
    """Resolve a canonical packet-relative path without accepting traversal."""

    relative = Path(value)
    if (
        not value
        or "\\" in value
        or relative.is_absolute()
        or bool(relative.drive)
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{label} must be a canonical packet-relative path: {value}")
    packet_root_resolved = packet_root.resolve()
    resolved = (packet_root / relative).resolve()
    try:
        resolved.relative_to(packet_root_resolved)
    except ValueError as exc:
        raise ValueError(f"{label} must remain inside the bound packet: {value}") from exc
    return resolved


def _load_math_decision_packet(
    root: Path,
    source: Path,
    source_sha256: str,
    decision: MathReviewDecision,
    unit: SourceUnit,
) -> None:
    """Validate a decision against one immutable, project-owned manual packet."""

    if (
        decision.packet_id is None
        or decision.packet_sha256 is None
        or decision.manifest_sha256 is None
        or decision.review_crop_path is None
    ):
        raise ValueError("incomplete packet binding")
    if decision.candidate_id is not None:
        raise ValueError("manual packet decisions must not select a candidate")
    if decision.disposition not in {
        MathReviewDisposition.MANUAL,
        MathReviewDisposition.REJECTED,
    }:
        raise ValueError("candidate-free packet decisions must be manual or rejected")

    manifests = _packet_manifest_candidates(root, decision.packet_id)
    if not manifests:
        raise ValueError(f"unknown math review packet {decision.packet_id}")
    if len(manifests) != 1:
        raise ValueError(f"ambiguous math review packet {decision.packet_id}")
    manifest_path = manifests[0]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid math review packet manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("math review packet manifest must contain an object")
    allowed_manifest_fields = {
        "schema_version",
        "kind",
        "source_pdf",
        "page_range",
        "pages",
        "math_unit_count",
        "unit_sequence",
        "units",
        "verification",
        "limits",
        "blockers",
        "result",
        "review_mode",
        "packet_id",
        "packet_sha256",
        "manifest_sha256",
    }
    optional_manifest_fields = {"explicit_unit_ids"}
    if not (
        set(manifest) == allowed_manifest_fields
        or set(manifest) == allowed_manifest_fields | optional_manifest_fields
    ):
        raise ValueError("manual packet contains unsupported or missing manifest fields")
    if manifest.get("schema_version") != 1:
        raise ValueError("manual packet schema version is unsupported")

    recorded_manifest_sha256 = manifest.get("manifest_sha256")
    manifest_core = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    actual_manifest_sha256 = _canonical_sha256(manifest_core)
    if (
        not isinstance(recorded_manifest_sha256, str)
        or recorded_manifest_sha256 != actual_manifest_sha256
        or decision.manifest_sha256 != actual_manifest_sha256
    ):
        raise ValueError("packet manifest canonical hash is stale or invalid")

    recorded_packet_sha256 = manifest.get("packet_sha256")
    packet_payload = {
        key: value
        for key, value in manifest.items()
        if key not in {"packet_id", "packet_sha256", "manifest_sha256"}
    }
    actual_packet_sha256 = _canonical_sha256(packet_payload)
    if (
        not isinstance(recorded_packet_sha256, str)
        or recorded_packet_sha256 != actual_packet_sha256
        or decision.packet_sha256 != actual_packet_sha256
    ):
        raise ValueError("packet canonical hash is stale or invalid")
    if manifest.get("packet_id") != decision.packet_id:
        raise ValueError("packet manifest ID does not match review decision")
    if decision.packet_id != f"math-review-{actual_packet_sha256[:20]}":
        raise ValueError("packet ID is not derived from its canonical content hash")
    if manifest.get("kind") != "immutable-math-visual-review-packet":
        raise ValueError("review decision is bound to the wrong packet kind")
    if manifest.get("review_mode") != "manual-only":
        raise ValueError("review decision is not bound to a manual-only packet")
    if manifest.get("blockers") != []:
        raise ValueError("manual packet contains unsupported blocker or result claims")
    result = manifest.get("result")
    if (
        not isinstance(result, dict)
        or result.get("decisions_generated") is not False
        or set(result) - {"directory", "decisions_generated"}
    ):
        raise ValueError("manual packet must not contain decisions or verified results")

    source_binding = manifest.get("source_pdf")
    try:
        current_source_path = source.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("manual packet source PDF must remain inside the project") from exc
    if (
        not isinstance(source_binding, dict)
        or set(source_binding) != {"configured_path", "project_path", "sha256"}
        or source_binding.get("sha256") != source_sha256
        or source_binding.get("project_path") != current_source_path
    ):
        raise ValueError("packet source PDF binding is stale or invalid")

    raw_units = manifest.get("units")
    if not isinstance(raw_units, list):
        raise ValueError("packet units must be a list")
    matches: list[dict[str, Any]] = []
    for raw_entry in raw_units:
        if not isinstance(raw_entry, dict):
            raise ValueError("packet unit entries must be objects")
        raw_unit = raw_entry.get("unit")
        if isinstance(raw_unit, dict) and raw_unit.get("unit_id") == decision.unit_id:
            matches.append(raw_entry)
    if len(matches) != 1:
        raise ValueError("review unit is missing or duplicated in the bound packet")
    if manifest.get("math_unit_count") != len(raw_units):
        raise ValueError("packet math unit count is stale or invalid")
    unit_sequence = manifest.get("unit_sequence")
    if not isinstance(unit_sequence, list):
        raise ValueError("packet unit sequence is invalid")
    explicit_unit_ids = _explicit_packet_unit_ids(manifest, unit_sequence, "packet")
    entry = matches[0]
    allowed_entry_fields = {
        "unit",
        "source_hash",
        "unit_record_sha256",
        "assets",
        "candidates",
        "review_crop",
    }
    if set(entry) - allowed_entry_fields:
        raise ValueError("packet unit record contains unsupported review-result fields")
    raw_unit = entry.get("unit")
    current_unit = unit.model_dump(mode="json")
    if raw_unit != current_unit:
        raise ValueError("packet unit snapshot does not match the current source unit")
    if entry.get("source_hash") != unit.source_hash:
        raise ValueError("packet unit source hash is stale")
    if entry.get("unit_record_sha256") != _canonical_sha256(current_unit):
        raise ValueError("packet unit record canonical hash is stale or invalid")
    if entry.get("candidates") != []:
        raise ValueError("manual-only packet unit must not contain candidate conclusions")
    assets = entry.get("assets")
    if not isinstance(assets, list) or any(
        not isinstance(asset, dict)
        or set(asset) != {"kind", "project_path", "packet_path", "sha256"}
        for asset in assets
    ):
        raise ValueError("packet unit assets contain unsupported evidence fields")
    if not _needs_math_review(unit) and unit.unit_id not in explicit_unit_ids:
        raise ValueError("manual packet unit is neither reviewable math nor explicitly included")
    if not isinstance(unit_sequence, list) or unit_sequence.count(unit.unit_id) != 1:
        raise ValueError("packet unit sequence does not uniquely bind the review unit")

    review_crop = entry.get("review_crop")
    if not isinstance(review_crop, dict):
        raise ValueError("packet unit has no bound review crop")
    if set(review_crop) != {
        "packet_path",
        "sha256",
        "source_pdf_page",
        "unit_bbox",
        "clip_bbox",
        "render",
    }:
        raise ValueError("packet review crop contains unsupported evidence fields")
    if review_crop.get("packet_path") != decision.review_crop_path:
        raise ValueError("review crop path does not match the bound packet unit")
    if review_crop.get("sha256") != decision.crop_sha256:
        raise ValueError("review crop hash does not match the bound packet unit")
    if review_crop.get("source_pdf_page") != decision.page:
        raise ValueError("review crop page does not match the decision")
    if review_crop.get("unit_bbox") != list(unit.bbox):
        raise ValueError("review crop bbox does not match the current unit")
    render = review_crop.get("render")
    if render != {
        "engine": "PyMuPDF",
        "matrix": [MANUAL_REVIEW_CROP_SCALE, MANUAL_REVIEW_CROP_SCALE],
        "padding_points": MANUAL_REVIEW_CROP_PADDING_PT,
        "alpha": False,
    }:
        raise ValueError("review crop rendering contract is stale or invalid")
    crop_file = _safe_packet_file(
        manifest_path.parent, decision.review_crop_path, "review crop path"
    )
    if not crop_file.is_file():
        raise ValueError("bound review crop is missing")

    raw_pages = manifest.get("pages")
    if not isinstance(raw_pages, list):
        raise ValueError("packet pages must be a list")
    page_matches = [
        item for item in raw_pages if isinstance(item, dict) and item.get("page") == decision.page
    ]
    if len(page_matches) != 1:
        raise ValueError("review page is missing or duplicated in the bound packet")
    page_entry = page_matches[0]
    if set(page_entry) != {"page", "image", "review_unit_ids"}:
        raise ValueError("packet page contains unsupported review-result fields")
    review_unit_ids = page_entry.get("review_unit_ids")
    if not isinstance(review_unit_ids, list) or review_unit_ids.count(unit.unit_id) != 1:
        raise ValueError("packet page does not uniquely bind the review unit")
    page_image = page_entry.get("image")
    if not isinstance(page_image, dict):
        raise ValueError("packet page image binding is missing")
    if set(page_image) != {"source_pdf_page", "packet_path", "sha256", "render"}:
        raise ValueError("packet page image contains unsupported evidence fields")
    if page_image.get("sha256") != decision.page_image_sha256:
        raise ValueError("packet page image hash does not match the review decision")
    if page_image.get("source_pdf_page") != decision.page:
        raise ValueError("packet page image source page is stale")
    if page_image.get("render") != {
        "engine": "PyMuPDF",
        "matrix": [2.0, 2.0],
        "alpha": False,
    }:
        raise ValueError("packet page rendering contract is stale or invalid")
    page_path_value = page_image.get("packet_path")
    if not isinstance(page_path_value, str):
        raise ValueError("packet page image path is invalid")
    page_file = _safe_packet_file(manifest_path.parent, page_path_value, "page image path")
    if not page_file.is_file():
        raise ValueError("bound packet page image is missing")

    try:
        with fitz.open(source) as document:
            expected_crop, expected_crop_sha256, expected_clip = _manual_review_crop(document, unit)
            expected_page, expected_page_sha256 = _render_full_page(document, decision.page)
    except (OSError, ValueError, IndexError) as exc:
        raise ValueError(f"unable to reproduce packet PDF evidence: {exc}") from exc
    if review_crop.get("clip_bbox") != list(expected_clip):
        raise ValueError("review crop clip bbox is stale or invalid")
    if (
        expected_crop_sha256 != decision.crop_sha256
        or crop_file.read_bytes() != expected_crop
        or sha256_file(crop_file) != decision.crop_sha256
    ):
        raise ValueError("review crop is stale, swapped, or tampered")
    if (
        expected_page_sha256 != decision.page_image_sha256
        or page_file.read_bytes() != expected_page
        or sha256_file(page_file) != decision.page_image_sha256
    ):
        raise ValueError("packet page image is stale, swapped, or tampered")


def _render_page_at_scale(document: fitz.Document, page: int, scale: float) -> tuple[bytes, str]:
    pixmap = document.load_page(page - 1).get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    content = pixmap.tobytes("png")
    return content, hashlib.sha256(content).hexdigest()


def _validate_structural_page_binding(
    root: Path,
    packet_root: Path,
    document: fitz.Document,
    page: int,
    image: dict[str, Any],
    *,
    manual_packet: bool,
) -> str:
    """Validate one packet page against both its file and the current PDF."""

    if page < 1 or page > document.page_count:
        raise ValueError(f"Packet page {page} is outside the current source PDF")
    if manual_packet:
        if set(image) != {"source_pdf_page", "packet_path", "sha256", "render"}:
            raise ValueError("Packet page image contains unsupported evidence fields")
        if image.get("source_pdf_page") != page:
            raise ValueError("Packet page image source page is stale")
        if image.get("render") != {
            "engine": "PyMuPDF",
            "matrix": [2.0, 2.0],
            "alpha": False,
        }:
            raise ValueError("Packet page rendering contract is stale or invalid")
        path_value = image.get("packet_path")
        if not isinstance(path_value, str):
            raise ValueError("Packet page image path is invalid")
        image_path = _safe_packet_file(packet_root, path_value, "page image path")
        expected_content, expected_sha256 = _render_full_page(document, page)
    else:
        if set(image) != {"project_path", "sha256"}:
            raise ValueError("Packet page image contains unsupported evidence fields")
        path_value = image.get("project_path")
        if not isinstance(path_value, str):
            raise ValueError("Packet page image path is invalid")
        image_path = _safe_project_file(root, path_value, "packet page image path")
        # Pilot packets historically reused the 1.5x verification image when
        # present and otherwise rendered a private 2x image.  Reproduce both
        # documented contracts instead of trusting whichever file occupies
        # the manifest path.
        rendered = (
            _render_page_at_scale(document, page, 1.5),
            _render_page_at_scale(document, page, 2.0),
        )
        recorded_sha256 = image.get("sha256")
        matching = [value for value in rendered if value[1] == recorded_sha256]
        if len(matching) != 1:
            raise ValueError("Packet page image is stale or has an invalid render binding")
        expected_content, expected_sha256 = matching[0]
    recorded_sha256 = image.get("sha256")
    if not isinstance(recorded_sha256, str) or recorded_sha256 != expected_sha256:
        raise ValueError("Packet page image hash is stale or invalid")
    if (
        not image_path.is_file()
        or image_path.read_bytes() != expected_content
        or sha256_file(image_path) != expected_sha256
    ):
        raise ValueError("Packet page image is stale, swapped, or tampered")
    return expected_sha256


def _validate_packet_asset(root: Path, binding: Any, label: str) -> None:
    if not isinstance(binding, dict) or set(binding) != {"project_path", "sha256"}:
        raise ValueError(f"{label} binding contains unsupported evidence fields")
    path_value = binding.get("project_path")
    expected = binding.get("sha256")
    if not isinstance(path_value, str) or not isinstance(expected, str):
        raise ValueError(f"{label} binding is invalid")
    path = _safe_project_file(root, path_value, f"{label} path")
    if not path.is_file() or sha256_file(path) != expected:
        raise ValueError(f"{label} is stale, swapped, or tampered")


def _load_structural_packet(
    root: Path,
    source: Path,
    source_sha256: str,
    sidecar: MathStructuralReviewSidecar,
    units_by_id: dict[str, SourceUnit],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[int, str]]:
    """Load and fully validate the packet bound to a structural sidecar."""

    manifests = _packet_manifest_candidates(root, sidecar.packet_id)
    if not manifests:
        raise ValueError(f"Unknown structural review packet: {sidecar.packet_id}")
    if len(manifests) != 1:
        raise ValueError(f"Ambiguous structural review packet: {sidecar.packet_id}")
    manifest_path = manifests[0]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid packet manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"Packet manifest must contain an object: {manifest_path}")

    payload_value = manifest.get("payload")
    manual_packet = not isinstance(payload_value, dict)
    if isinstance(payload_value, dict):
        if set(manifest) != {"payload", "payload_sha256"}:
            raise ValueError("Packet manifest contains unsupported evidence fields")
        payload = payload_value
        packet_id = payload.get("packet_id")
        recorded_hash = manifest.get("payload_sha256")
        actual_hash = _canonical_sha256(payload)
    else:
        allowed_manifest_fields = {
            "schema_version",
            "kind",
            "source_pdf",
            "page_range",
            "pages",
            "math_unit_count",
            "unit_sequence",
            "units",
            "verification",
            "limits",
            "blockers",
            "result",
            "review_mode",
            "packet_id",
            "packet_sha256",
            "manifest_sha256",
        }
        optional_manifest_fields = {"explicit_unit_ids"}
        if not (
            set(manifest) == allowed_manifest_fields
            or set(manifest) == allowed_manifest_fields | optional_manifest_fields
        ):
            raise ValueError("Manual packet contains unsupported or missing manifest fields")
        packet_id = manifest.get("packet_id")
        recorded_hash = manifest.get("packet_sha256")
        payload = {
            key: value
            for key, value in manifest.items()
            if key not in {"packet_id", "packet_sha256", "manifest_sha256"}
        }
        actual_hash = _canonical_sha256(payload)
        manifest_core = {
            **payload,
            "packet_id": packet_id,
            "packet_sha256": recorded_hash,
        }
        if manifest.get("manifest_sha256") != _canonical_sha256(manifest_core):
            raise ValueError("Packet manifest canonical hash is stale or invalid")
    if packet_id != sidecar.packet_id:
        raise ValueError("Packet manifest ID does not match structural sidecar")
    if not isinstance(recorded_hash, str) or recorded_hash != actual_hash:
        raise ValueError("Packet payload canonical hash is stale or invalid")
    if sidecar.packet_payload_sha256 != actual_hash:
        raise ValueError("Structural sidecar packet payload hash is stale or invalid")

    if manual_packet:
        if packet_id != f"math-review-{actual_hash[:20]}":
            raise ValueError("Packet ID is not derived from its canonical content hash")
        if payload.get("kind") != "immutable-math-visual-review-packet":
            raise ValueError("Structural sidecar is bound to the wrong packet kind")
        if payload.get("review_mode") != "manual-only":
            raise ValueError("Structural sidecar is not bound to a manual-only packet")
        result = payload.get("result")
        if (
            payload.get("blockers") != []
            or not isinstance(result, dict)
            or result.get("decisions_generated") is not False
            or set(result) - {"directory", "decisions_generated"}
        ):
            raise ValueError("Manual packet contains unsupported result claims")

    source_binding = payload.get("source_pdf")
    try:
        source_project_path = source.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("Packet source PDF must remain inside the project") from exc
    expected_source_fields = (
        {"configured_path", "project_path", "sha256"}
        if manual_packet
        else {"project_path", "sha256"}
    )
    if (
        not isinstance(source_binding, dict)
        or set(source_binding) != expected_source_fields
        or source_binding.get("project_path") != source_project_path
        or source_binding.get("sha256") != source_sha256
    ):
        raise ValueError("Structural review packet source PDF binding is stale or invalid")

    units: dict[str, dict[str, Any]] = {}
    raw_units = payload.get("units", [])
    if not isinstance(raw_units, list):
        raise ValueError("Packet payload units must be a list")
    page_hashes: dict[int, str] = {}
    review_unit_ids_by_page: dict[int, list[str]] = {}
    try:
        with fitz.open(source) as document:
            if manual_packet:
                raw_pages = payload.get("pages")
                if not isinstance(raw_pages, list):
                    raise ValueError("Packet pages must be a list")
                page_range = payload.get("page_range")
                if (
                    not isinstance(page_range, dict)
                    or set(page_range) != {"start", "end"}
                    or not isinstance(page_range.get("start"), int)
                    or not isinstance(page_range.get("end"), int)
                    or page_range["start"] < 1
                    or page_range["end"] < page_range["start"]
                ):
                    raise ValueError("Packet page range is stale or invalid")
                expected_pages = list(range(page_range["start"], page_range["end"] + 1))
                recorded_pages = [
                    entry.get("page") if isinstance(entry, dict) else None for entry in raw_pages
                ]
                if recorded_pages != expected_pages:
                    raise ValueError(
                        "Packet pages must uniquely cover the complete consecutive page range"
                    )
                for page_entry in raw_pages:
                    if not isinstance(page_entry, dict) or set(page_entry) != {
                        "page",
                        "image",
                        "review_unit_ids",
                    }:
                        raise ValueError("Packet page contains unsupported evidence fields")
                    page = page_entry.get("page")
                    review_unit_ids = page_entry.get("review_unit_ids")
                    image = page_entry.get("image")
                    if (
                        not isinstance(page, int)
                        or page in page_hashes
                        or not isinstance(review_unit_ids, list)
                        or any(not isinstance(value, str) for value in review_unit_ids)
                        or len(review_unit_ids) != len(set(review_unit_ids))
                        or not isinstance(image, dict)
                    ):
                        raise ValueError("Packet page binding is invalid or duplicated")
                    page_hashes[page] = _validate_structural_page_binding(
                        root,
                        manifest_path.parent,
                        document,
                        page,
                        image,
                        manual_packet=True,
                    )
                    review_unit_ids_by_page[page] = review_unit_ids

            for entry in raw_units:
                if not isinstance(entry, dict):
                    raise ValueError("Packet payload unit entries must be objects")
                if manual_packet and set(entry) != {
                    "unit",
                    "source_hash",
                    "unit_record_sha256",
                    "assets",
                    "candidates",
                    "review_crop",
                }:
                    raise ValueError(
                        "Manual packet unit contains unsupported or missing evidence fields"
                    )
                raw_unit = entry.get("unit", entry.get("source_unit"))
                if not isinstance(raw_unit, dict) or not isinstance(raw_unit.get("unit_id"), str):
                    raise ValueError("Packet payload contains an invalid unit snapshot")
                unit_id = raw_unit["unit_id"]
                if unit_id in units:
                    raise ValueError(f"Packet payload contains duplicate unit ID: {unit_id}")
                current_unit = units_by_id.get(unit_id)
                if current_unit is None or raw_unit != current_unit.model_dump(mode="json"):
                    raise ValueError(f"Packet unit snapshot for {unit_id} is stale or invalid")
                source_hash = entry.get("source_hash", raw_unit.get("source_hash"))
                if source_hash != current_unit.source_hash:
                    raise ValueError(f"Packet unit source hash for {unit_id} is stale")
                record_hash = entry.get(
                    "unit_record_sha256", entry.get("source_unit_record_sha256")
                )
                if (manual_packet or record_hash is not None) and record_hash != _canonical_sha256(
                    raw_unit
                ):
                    raise ValueError(
                        f"Packet unit record canonical hash for {unit_id} is stale or invalid"
                    )
                page = current_unit.page
                if manual_packet:
                    if entry.get("candidates") != []:
                        raise ValueError("Manual packet unit contains candidate conclusions")
                    if review_unit_ids_by_page.get(page, []).count(unit_id) != 1:
                        raise ValueError(
                            f"Packet page does not uniquely bind review unit {unit_id}"
                        )
                    page_hash = page_hashes.get(page)
                    review_crop = entry.get("review_crop")
                    if not isinstance(review_crop, dict) or set(review_crop) != {
                        "packet_path",
                        "sha256",
                        "source_pdf_page",
                        "unit_bbox",
                        "clip_bbox",
                        "render",
                    }:
                        raise ValueError("Packet review crop binding is invalid")
                    if (
                        review_crop.get("source_pdf_page") != page
                        or review_crop.get("unit_bbox") != list(current_unit.bbox)
                        or review_crop.get("render")
                        != {
                            "engine": "PyMuPDF",
                            "matrix": [
                                MANUAL_REVIEW_CROP_SCALE,
                                MANUAL_REVIEW_CROP_SCALE,
                            ],
                            "padding_points": MANUAL_REVIEW_CROP_PADDING_PT,
                            "alpha": False,
                        }
                    ):
                        raise ValueError("Packet review crop binding is stale or invalid")
                    crop_path_value = review_crop.get("packet_path")
                    if not isinstance(crop_path_value, str):
                        raise ValueError("Packet review crop path is invalid")
                    crop_path = _safe_packet_file(
                        manifest_path.parent, crop_path_value, "review crop path"
                    )
                    crop, crop_sha256, clip_bbox = _manual_review_crop(document, current_unit)
                    if (
                        review_crop.get("clip_bbox") != list(clip_bbox)
                        or review_crop.get("sha256") != crop_sha256
                        or not crop_path.is_file()
                        or crop_path.read_bytes() != crop
                        or sha256_file(crop_path) != crop_sha256
                    ):
                        raise ValueError("Packet review crop is stale, swapped, or tampered")
                else:
                    page_image = entry.get("full_page_image")
                    if not isinstance(page_image, dict):
                        raise ValueError("Packet unit has no full-page image binding")
                    page_hash = _validate_structural_page_binding(
                        root,
                        manifest_path.parent,
                        document,
                        page,
                        page_image,
                        manual_packet=False,
                    )
                    previous_hash = page_hashes.setdefault(page, page_hash)
                    if previous_hash != page_hash:
                        raise ValueError("Packet contains conflicting page image bindings")
                    candidates = entry.get("candidates", [])
                    if not isinstance(candidates, list):
                        raise ValueError("Packet unit candidates must be a list")
                    for candidate in candidates:
                        if not isinstance(candidate, dict):
                            raise ValueError("Packet candidate entry must be an object")
                        candidate_record = candidate.get("candidate")
                        if not isinstance(candidate_record, dict) or candidate.get(
                            "candidate_record_sha256"
                        ) != _canonical_sha256(candidate_record):
                            raise ValueError("Packet candidate canonical hash is stale or invalid")
                        _validate_packet_asset(root, candidate.get("crop"), "Packet crop")
                        _validate_packet_asset(
                            root, candidate.get("raw_response"), "Packet raw response"
                        )
                units[unit_id] = {
                    "unit": raw_unit,
                    "source_hash": source_hash,
                    "page_image_sha256": page_hash,
                }
    except (OSError, IndexError) as exc:
        raise ValueError(f"Unable to reproduce structural packet evidence: {exc}") from exc

    unit_sequence = payload.get("unit_sequence")
    if (manual_packet or unit_sequence is not None) and unit_sequence != list(units):
        raise ValueError("Packet unit sequence is stale or invalid")
    if manual_packet:
        explicit_unit_ids = _explicit_packet_unit_ids(payload, list(units), "Packet")
        for unit_id in units:
            current_unit = units_by_id[unit_id]
            if not _needs_math_review(current_unit) and unit_id not in explicit_unit_ids:
                raise ValueError(
                    f"Packet unit {unit_id} is neither reviewable math nor explicitly included"
                )
        # Empty pages are legitimate members of a complete consecutive packet.
        # Seed every validated manifest page so equality still rejects an
        # injected ID on an empty page, a missing ID, or reordered IDs.
        expected_ids_by_page: dict[int, list[str]] = {page: [] for page in review_unit_ids_by_page}
        for unit_id, entry in units.items():
            expected_ids_by_page[entry["unit"]["page"]].append(unit_id)
        if review_unit_ids_by_page != expected_ids_by_page:
            raise ValueError("Packet page review unit sequence is stale or invalid")
    recorded_unit_count = payload.get("math_unit_count", payload.get("unit_count"))
    if recorded_unit_count is not None and recorded_unit_count != len(units):
        raise ValueError("Packet unit count is stale or invalid")
    return payload, units, page_hashes


def _safe_project_file(root: Path, value: str, label: str) -> Path:
    """Resolve a project-relative evidence path and reject traversal."""

    candidate = Path(value)
    root_resolved = root.resolve()
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"{label} must remain inside the project: {value}") from exc
    return resolved


def _relative_url(from_dir: Path, target: Path) -> str:
    """Return a local, HTML-escaped relative path (never a URL)."""

    return html.escape(os.path.relpath(target, from_dir).replace("\\", "/"), quote=True)


def _ensure_page_image(root: Path, source: Path, page_number: int) -> Path:
    """Ensure the report's local page image exists, without touching the ledger."""

    image_path = root / PAGE_IMAGE_DIR / f"page-{page_number:04}.png"
    document = fitz.open(source)
    try:
        if page_number < 1 or page_number > document.page_count:
            raise ValueError(f"Page {page_number} is outside the source PDF")
        content = (
            document[page_number - 1]
            .get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            .tobytes("png")
        )
    finally:
        document.close()
    # Recompute the expected bytes from the PDF.  This prevents a pre-existing
    # or tampered page image from becoming trusted review evidence merely
    # because it occupied the expected filename.
    if not image_path.is_file() or image_path.read_bytes() != content:
        atomic_write_bytes(image_path, content)
    return image_path


def _unit_crop_files(root: Path, unit: SourceUnit) -> list[tuple[str, Path | None]]:
    result: list[tuple[str, Path | None]] = []
    for asset in unit.asset_refs:
        try:
            path = _safe_project_file(root, asset.path, "unit crop path")
        except ValueError:
            path = None
        result.append((str(asset.path), path))
    return result


def _render_mathml(latex: str, *, display: str = "block") -> str:
    """Render a candidate formula, or make a visible local error when it fails."""

    try:
        rendered = latex_to_mathml(latex, display=display)
        # The namespace is not a network dependency, but dropping it makes it
        # impossible for a simplistic local-report check to mistake it for a
        # remote resource.  HTML5 still treats this as embedded MathML.
        rendered = re.sub(r"\s+xmlns=(['\"]).*?\1", "", rendered, count=1)
        return rendered
    except Exception as exc:  # conversion failures are evidence, not fatal reports
        message = html.escape(str(exc) or exc.__class__.__name__)
        return f'<div class="math-error">MathML render error: {message}</div>'


def _candidate_mathml(candidate: MathCandidate) -> str:
    if candidate.latex and candidate.latex.strip():
        return _render_mathml(candidate.latex, display="block")
    if candidate.source_markdown:
        matches = list(_INLINE_MATH_RE.finditer(candidate.source_markdown))
        if matches:
            return " ".join(_render_mathml(match.group(1), display="inline") for match in matches)
    return '<div class="math-error">MathML render error: candidate has no renderable formula</div>'


def _candidate_freshness(
    root: Path,
    candidate: MathCandidate,
    source_pdf_sha256: str,
    unit: SourceUnit | None,
    page_image_hashes: dict[int, str] | None = None,
) -> list[str]:
    """Return evidence freshness problems for report annotations."""

    problems: list[str] = []
    if candidate.source_pdf_sha256 != source_pdf_sha256:
        problems.append("source PDF hash is stale")
    if unit is None:
        problems.append("unit is missing")
    else:
        if candidate.unit_id != unit.unit_id:
            problems.append("candidate unit does not match")
        if candidate.page != unit.page:
            problems.append("candidate page does not match unit")
        if sha256_text(unit.source_text) != unit.source_hash:
            problems.append("current unit source_hash does not match source_text")
        if candidate.source_hash != unit.source_hash:
            problems.append("unit source hash is stale")
    try:
        crop_path = _safe_project_file(root, candidate.crop_path, "candidate crop path")
        if not crop_path.is_file():
            problems.append("candidate crop is missing")
        elif sha256_file(crop_path) != candidate.crop_sha256:
            problems.append("candidate crop hash is stale")
    except (OSError, ValueError) as exc:
        problems.append(str(exc))
    try:
        raw_path = _safe_project_file(root, candidate.raw_response_path, "candidate response path")
        if not raw_path.is_file():
            problems.append("candidate raw response is missing")
        elif sha256_file(raw_path) != candidate.response_sha256:
            problems.append("candidate response hash is stale")
    except (OSError, ValueError) as exc:
        problems.append(str(exc))
    if page_image_hashes is not None:
        actual_page_hash = page_image_hashes.get(candidate.page)
        if actual_page_hash is None:
            problems.append("page image is missing")
    return problems


def _verification_overlaps(payload: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """Collect overlap groups from both current and older verification shapes."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    errors = payload.get("errors", [])
    if isinstance(errors, list):
        for error in errors:
            if not isinstance(error, dict):
                continue
            code = str(error.get("code", "")).casefold()
            if "overlap" not in code:
                continue
            page = error.get("page")
            if isinstance(page, int):
                grouped[page].append(error)
    explicit = payload.get("overlap_groups", [])
    if isinstance(explicit, list):
        for group in explicit:
            if not isinstance(group, dict):
                continue
            page = group.get("page")
            if isinstance(page, int):
                grouped[page].append(group)
    return {page: values for page, values in sorted(grouped.items())}


def _load_verification(root: Path) -> tuple[dict[str, Any], str | None]:
    path = root / "derived" / "verification.json"
    if not path.is_file():
        return {}, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"Invalid verification evidence: {path}: {exc}"
    if not isinstance(payload, dict):
        return {}, f"Invalid verification evidence: {path} must contain an object"
    return payload, None


def _unit_current_html(unit: SourceUnit) -> str:
    def field(label: str, value: Any) -> str:
        if value is None or value == "":
            shown = '<span class="muted">(none)</span>'
        else:
            shown = f"<pre>{html.escape(str(value))}</pre>"
        return f"<dt>{html.escape(label)}</dt><dd>{shown}</dd>"

    return (
        '<dl class="current-fields">'
        + field("source_text", unit.source_text)
        + field("source_markdown", unit.source_markdown)
        + field("latex", unit.latex)
        + field("equation_number", unit.equation_number)
        + "</dl>"
    )


def _unit_crop_html(root: Path, report_dir: Path, unit: SourceUnit) -> str:
    crops: list[str] = []
    for display_path, path in _unit_crop_files(root, unit):
        if path is None or not path.is_file():
            crops.append(
                f'<div class="missing-crop">Unit crop missing: {html.escape(display_path)}</div>'
            )
            continue
        crops.append(
            f'<figure><img class="crop" src="{_relative_url(report_dir, path)}" alt="Unit crop">'
            f"<figcaption>{html.escape(display_path)}</figcaption></figure>"
        )
    if not crops:
        return '<div class="muted">No unit crop recorded.</div>'
    return '<div class="crops">' + "".join(crops) + "</div>"


def _candidate_html(
    root: Path,
    report_dir: Path,
    candidate: MathCandidate,
    freshness_problems: list[str],
) -> str:
    try:
        crop_path = _safe_project_file(root, candidate.crop_path, "candidate crop path")
    except ValueError:
        crop_path = None
    if crop_path is not None and crop_path.is_file():
        crop = (
            f'<img class="crop" src="{_relative_url(report_dir, crop_path)}" alt="Candidate crop">'
        )
    else:
        crop = f'<div class="missing-crop">Candidate crop missing: {html.escape(candidate.crop_path)}</div>'
    representation = (
        f"<pre>{html.escape(candidate.latex)}</pre>"
        if candidate.latex
        else f"<pre>{html.escape(candidate.source_markdown or '')}</pre>"
    )
    uncertainties = (
        "<ul>"
        + "".join(f"<li>{html.escape(item)}</li>" for item in candidate.uncertainties)
        + "</ul>"
        if candidate.uncertainties
        else '<span class="muted">none</span>'
    )
    freshness = (
        '<div class="stale-evidence"><b>Stale/invalid evidence:</b><ul>'
        + "".join(f"<li>{html.escape(problem)}</li>" for problem in freshness_problems)
        + "</ul></div>"
        if freshness_problems
        else '<div class="fresh-evidence">Evidence hashes are current.</div>'
    )
    return (
        '<article class="candidate">'
        f"<h4>Candidate {html.escape(candidate.candidate_id)} · "
        f"{html.escape(candidate.provider)} / {html.escape(candidate.model)} · "
        f"pass {candidate.pass_index}</h4>"
        f"<p><b>classification:</b> {html.escape(_value(candidate.classification))}; "
        f"<b>equation_number:</b> {html.escape(candidate.equation_number or '(none)')}</p>"
        f"{freshness}"
        f'<div class="candidate-grid"><div>{crop}</div><div>'
        f"<b>candidate representation</b>{representation}"
        f'<b>MathML preview</b><div class="mathml">{_candidate_mathml(candidate)}</div>'
        f"<b>uncertainties</b>{uncertainties}</div></div>"
        "</article>"
    )


def build_math_review_report(root: Path, page_spec: str = "all") -> dict[str, Any]:
    """Build the report while serializing its page-image and HTML writes."""

    root = Path(root).resolve()
    with project_write_lock(root):
        return _build_math_review_report_locked(root, page_spec)


def _build_math_review_report_locked(root: Path, page_spec: str) -> dict[str, Any]:
    """Build a self-contained, local-only comparison report for math evidence."""

    config = load_project(root)
    source = config.source(root)
    source_sha256 = sha256_file(source)
    if source_sha256 != config.source_sha256:
        raise ValueError("Source PDF hash changed after project initialization")
    units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    candidates = _read_candidates(root)
    document = fitz.open(source)
    try:
        pages = parse_page_spec(page_spec, document.page_count)
        page_dimensions = {
            page: (document[page - 1].rect.width, document[page - 1].rect.height) for page in pages
        }
        image_paths = {page: _ensure_page_image(root, source, page) for page in pages}
        page_image_hashes = {
            page: sha256_file(image_path) for page, image_path in image_paths.items()
        }
    finally:
        document.close()

    by_unit = {unit.unit_id: unit for unit in units}
    units_by_page: dict[int, list[SourceUnit]] = defaultdict(list)
    for unit in units:
        if unit.page in page_dimensions:
            units_by_page[unit.page].append(unit)
    candidates_by_unit: dict[str, list[MathCandidate]] = defaultdict(list)
    for recorded_candidate in candidates:
        matched_unit = by_unit.get(recorded_candidate.unit_id)
        if matched_unit is not None and matched_unit.page in page_dimensions:
            candidates_by_unit[recorded_candidate.unit_id].append(recorded_candidate)
    for values in candidates_by_unit.values():
        values.sort(key=lambda item: (item.pass_index, item.candidate_id))

    verification, verification_error = _load_verification(root)
    overlaps = _verification_overlaps(verification)
    report_path = root / REPORT_PATH
    report_dir = report_path.parent
    sections: list[str] = []
    for page in pages:
        page_width, page_height = page_dimensions[page]
        page_units = sorted(
            units_by_page.get(page, []), key=lambda item: (item.bbox[1], item.bbox[0])
        )
        boxes: list[str] = []
        for unit in page_units:
            x0, y0, x1, y1 = unit.bbox
            left = x0 / max(page_width, 1) * 100
            top = y0 / max(page_height, 1) * 100
            width = (x1 - x0) / max(page_width, 1) * 100
            height = (y1 - y0) / max(page_height, 1) * 100
            boxes.append(
                f'<a class="box" href="#{html.escape(unit.unit_id)}" '
                f'title="{html.escape(unit.unit_id)}" '
                f'style="left:{left:.3f}%;top:{top:.3f}%;width:{width:.3f}%;height:{height:.3f}%">'
                "</a>"
            )
        overlap_html = ""
        if page in overlaps:
            items = []
            for group in overlaps[page]:
                ids = [
                    str(value)
                    for key, value in group.items()
                    if key.endswith("unit_id") and isinstance(value, str)
                ]
                if isinstance(group.get("unit_ids"), list):
                    ids.extend(str(value) for value in group["unit_ids"])
                items.append(
                    "<li>"
                    + html.escape(
                        ", ".join(dict.fromkeys(ids)) or json.dumps(group, ensure_ascii=False)
                    )
                    + "</li>"
                )
            overlap_html = (
                '<div class="overlaps"><b>verification.json overlap groups</b><ul>'
                + "".join(items)
                + "</ul></div>"
            )
        unit_articles: list[str] = []
        for unit in page_units:
            candidate_cards = candidates_by_unit.get(unit.unit_id, [])
            if candidate_cards:
                candidates_html = "".join(
                    f'<div class="candidate-label">Candidate {chr(65 + index)}</div>'
                    + _candidate_html(
                        root,
                        report_dir,
                        candidate,
                        _candidate_freshness(
                            root,
                            candidate,
                            source_sha256,
                            unit,
                            page_image_hashes,
                        ),
                    )
                    for index, candidate in enumerate(candidate_cards)
                )
            else:
                candidates_html = '<div class="muted">No DeepSeek candidate recorded.</div>'
            unit_articles.append(
                f'<article class="unit" id="{html.escape(unit.unit_id)}">'
                f"<h3>{html.escape(unit.unit_id)} · {html.escape(_value(unit.kind))} · p.{unit.page}</h3>"
                f"<p><b>bbox:</b> {html.escape(str(tuple(round(value, 3) for value in unit.bbox)))}</p>"
                f"{_unit_crop_html(root, report_dir, unit)}"
                f"<h4>Current source</h4>{_unit_current_html(unit)}"
                f"<h4>DeepSeek candidates A/B</h4>{candidates_html}"
                "</article>"
            )
        page_image = image_paths[page]
        sections.append(
            f'<section class="page-section"><h2>PDF page {page}</h2>{overlap_html}'
            f'<div class="page-frame"><img class="page-image" '
            f'data-path="derived/verification/page-{page:04}.png" '
            f'src="{_relative_url(report_dir, page_image)}" alt="PDF page {page}">'
            + "".join(boxes)
            + "</div>"
            + "".join(unit_articles)
            + "</section>"
        )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    verification_blocker = (
        '<div class="verification-blocker"><b>Verification evidence blocker:</b> '
        + html.escape(verification_error)
        + "</div>"
        if verification_error is not None
        else ""
    )
    document_html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>Math review</title>"
        "<style>"
        "body{font:14px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:1500px;color:#172033}"
        "h1,h2,h3,h4{line-height:1.2}.page-section{margin:3rem 0;border-top:2px solid #cbd5e1;padding-top:1rem}"
        ".page-frame{position:relative;display:inline-block;max-width:100%;border:1px solid #cbd5e1}"
        ".page-image{display:block;max-width:100%;height:auto}.box{position:absolute;border:2px solid #dc2626;box-sizing:border-box;background:#dc262622}"
        ".unit{margin:1.5rem 0;padding:1rem;border:1px solid #cbd5e1;border-radius:.5rem;background:#f8fafc}"
        ".candidate{margin:.8rem 0;padding:.8rem;border:1px solid #cbd5e1;background:white}.candidate-grid{display:grid;grid-template-columns:minmax(160px,28%) 1fr;gap:1rem}"
        ".crop{max-width:100%;height:auto;border:1px solid #94a3b8;background:#fff}.crops{display:flex;flex-wrap:wrap;gap:1rem}.crops figure{max-width:360px;margin:0}.crops figcaption{overflow-wrap:anywhere;font-size:12px;color:#475569}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2ff;padding:.5rem;border-radius:.25rem}.current-fields{display:grid;grid-template-columns:180px 1fr;gap:.2rem .8rem}.current-fields dt{font-weight:600}.current-fields dd{margin:0}"
        ".candidate-label{font-weight:700;color:#1d4ed8;margin-top:.8rem}.mathml{padding:.5rem;background:#f8fafc;overflow:auto}.math-error,.missing-crop,.stale-evidence,.verification-blocker{color:#b91c1c;background:#fef2f2;padding:.5rem;border:1px solid #dc2626}.fresh-evidence{color:#166534;background:#f0fdf4;padding:.5rem}.muted{color:#64748b}.overlaps{padding:.6rem 1rem;background:#fff7ed;border:1px solid #fb923c}.overlaps ul{margin:.25rem 0}"
        "</style></head><body><h1>Local mathematics candidate review</h1>"
        f"<p>Source PDF SHA-256: <code>{html.escape(source_sha256)}</code>. "
        "Candidates are evidence only; compare each crop and page against the PDF before importing.</p>"
        + verification_blocker
        + "".join(sections)
        + "</body></html>"
    )
    if sha256_file(source) != source_sha256:
        raise ValueError("Source PDF changed while the math review report was being built")
    atomic_write_text(report_path, document_html)
    return {
        "report_path": str(report_path),
        "visual_report": str(report_path),
        "pages": pages,
        "unit_count": sum(len(units_by_page.get(page, [])) for page in pages),
        "candidate_count": sum(
            len(candidates_by_unit.get(unit.unit_id, []))
            for page in pages
            for unit in units_by_page.get(page, [])
        ),
        "overlap_groups": sum(len(groups) for groups in overlaps.values()),
        "verification_blocker": verification_error,
        "source_sha256": source_sha256,
    }


def _specific_reason(reason: str) -> bool:
    normalized = " ".join(reason.casefold().split())
    return bool(normalized) and normalized not in _GENERIC_REASONS and len(normalized) >= 5


def _unit_crop_hash_matches(root: Path, unit: SourceUnit, expected: str) -> bool:
    expected = expected.casefold()
    for _, path in _unit_crop_files(root, unit):
        if path is not None and path.is_file():
            try:
                if sha256_file(path).casefold() == expected:
                    return True
            except OSError:
                continue
    return False


def _candidate_representation(
    decision: MathReviewDecision,
    candidate: MathCandidate | None,
    unit: SourceUnit,
) -> tuple[str | None, str | None, str | None]:
    """Return (latex, source_markdown, equation_number) for the override."""

    latex = decision.final_latex
    source_markdown = decision.final_source_markdown
    equation_number = decision.equation_number
    if candidate is not None:
        if latex is None:
            latex = candidate.latex
        if source_markdown is None:
            source_markdown = candidate.source_markdown
        if equation_number is None:
            equation_number = candidate.equation_number
    display = unit.kind is UnitKind.EQUATION
    classification = _value(candidate.classification) if candidate is not None else ""
    if not display and classification == MathCandidateClassification.DISPLAY.value:
        display = True
    if display:
        if not latex or not latex.strip():
            raise ValueError(f"Display math review {decision.decision_id} has no final latex")
        return latex.strip(), None, equation_number
    if not source_markdown or not source_markdown.strip():
        raise ValueError(f"Inline math review {decision.decision_id} has no final source_markdown")
    return None, source_markdown.strip(), None


def _freshness_errors(
    root: Path,
    source: Path,
    source_sha256: str,
    decision: MathReviewDecision,
    unit: SourceUnit,
    candidate: MathCandidate | None,
    *,
    packet_bound: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not decision.reviewed_against_pdf:
        errors.append("reviewed_against_pdf must be true")
    if not _specific_reason(decision.reason):
        errors.append("reason must be a concrete visual evidence reason")
    if decision.source_pdf_sha256 != source_sha256:
        errors.append("source PDF hash is stale")
    if decision.page != unit.page:
        errors.append("decision page does not match unit")
    if sha256_text(unit.source_text) != unit.source_hash:
        errors.append("current unit source_hash does not match source_text")
    if decision.source_hash != unit.source_hash:
        errors.append("unit source hash is stale")
    if not packet_bound:
        try:
            page_image = _ensure_page_image(root, source, decision.page)
            if sha256_file(page_image) != decision.page_image_sha256:
                errors.append("page image hash is stale")
        except (OSError, ValueError) as exc:
            errors.append(f"page image unavailable: {exc}")
    if candidate is not None and not packet_bound:
        if decision.candidate_id is not None and candidate.candidate_id != decision.candidate_id:
            errors.append("candidate_id does not identify the supplied candidate")
        errors.extend(_candidate_freshness(root, candidate, source_sha256, unit))
        if candidate.crop_sha256 != decision.crop_sha256:
            errors.append("candidate crop hash does not match review decision")
    elif not packet_bound and not _unit_crop_hash_matches(root, unit, decision.crop_sha256):
        errors.append("unit crop hash is stale or missing")
    return errors


def _canonical_decision_sha256(
    decision: MathReviewDecision | MathStructuralOverrideDecision,
) -> str:
    return _canonical_sha256(decision.model_dump(mode="json"))


def _load_existing_reviews(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    records: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            decision = MathReviewDecision.model_validate_json(line)
        except Exception as exc:
            raise ValueError(
                f"Invalid existing math review at {path}:{line_number}: {exc}"
            ) from exc
        if decision.decision_id in records:
            raise ValueError(f"Duplicate existing math decision_id: {decision.decision_id}")
        records[decision.decision_id] = _canonical_decision_sha256(decision)
    return records


def _load_layout(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"overrides": []}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload is None:
        return {"overrides": []}
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a YAML object")
    overrides = payload.get("overrides", [])
    if not isinstance(overrides, list):
        raise ValueError(f"{path} must contain an overrides list")
    return payload


def _decision_override(
    decision: MathReviewDecision,
    candidate: MathCandidate | None,
    unit: SourceUnit,
    source_sha256: str,
) -> dict[str, Any]:
    latex, source_markdown, equation_number = _candidate_representation(decision, candidate, unit)
    override: dict[str, Any] = {
        "unit_id": decision.unit_id,
        "verified": True,
        "reason": decision.reason.strip(),
        "math_review_decision_id": decision.decision_id,
        "candidate_id": decision.candidate_id,
        "source_pdf_sha256": source_sha256,
        "source_hash": decision.source_hash,
        "crop_sha256": decision.crop_sha256,
        "page_image_sha256": decision.page_image_sha256,
        "reviewed_against_pdf": True,
        "reviewer_id": decision.reviewer_id,
        "reviewed_at": decision.reviewed_at,
    }
    if unit.kind is UnitKind.EQUATION or latex is not None:
        override["latex"] = latex
        # Keep the number an independent field, never splice it into latex.
        override["equation_number"] = equation_number
    else:
        override["source_markdown"] = source_markdown
    reviewed_updates: dict[str, Any] = {
        "verification_status": SemanticStatus.VERIFIED,
        "confidence": 1.0,
    }
    for field in ("latex", "source_markdown", "equation_number"):
        if field in override:
            reviewed_updates[field] = override[field]
    effective_markdown = reviewed_updates.get("source_markdown", unit.source_markdown)
    if unit.kind is UnitKind.EQUATION or effective_markdown:
        reviewed_updates["math_status"] = SemanticStatus.VERIFIED
    reviewed_unit = SourceUnit.model_validate(
        unit.model_copy(update=reviewed_updates).model_dump(mode="python")
    )
    override["math_review_guard"] = {
        "schema_version": 1,
        "decision_id": decision.decision_id,
        "unit_id": unit.unit_id,
        "page": unit.page,
        "source_pdf_sha256": source_sha256,
        "source_hash": unit.source_hash,
        "unit_guard_sha256": canonical_math_review_unit_guard_sha256(unit),
        "before_representation_sha256": (canonical_math_review_representation_sha256(unit)),
        "after_representation_sha256": (canonical_math_review_representation_sha256(reviewed_unit)),
        "crop_sha256": decision.crop_sha256,
        "page_image_sha256": decision.page_image_sha256,
        "reviewed_against_pdf": True,
    }
    return override


def _structural_binding_errors(
    source: Path,
    source_sha256: str,
    structural: MathStructuralOverrideDecision,
    math_decision: MathReviewDecision | None,
    unit: SourceUnit | None,
    packet_units: dict[str, dict[str, Any]],
    packet_page_hashes: dict[int, str],
    units_by_id: dict[str, SourceUnit],
) -> list[str]:
    errors: list[str] = []
    if math_decision is None:
        errors.append("no same-batch MathReviewDecision has this decision_id")
    else:
        bound_fields = (
            "unit_id",
            "page",
            "source_pdf_sha256",
            "source_hash",
            "page_image_sha256",
            "reviewer_id",
            "reviewer_model",
            "reviewer_effort",
            "reviewer_task",
        )
        for field in bound_fields:
            if getattr(structural, field) != getattr(math_decision, field):
                errors.append(f"{field} does not match the same-batch math decision")
        if not math_decision.reviewed_against_pdf:
            errors.append("same-batch math decision was not reviewed against the PDF")
    if not structural.reviewed_against_pdf:
        errors.append("reviewed_against_pdf must be true")
    if not _specific_reason(structural.reason):
        errors.append("reason must be a concrete visual evidence reason")
    if structural.source_pdf_sha256 != source_sha256:
        errors.append("source PDF hash is stale")
    if unit is None:
        errors.append(f"unknown unit {structural.unit_id}")
    else:
        if structural.page != unit.page:
            errors.append("page does not match current unit")
        if sha256_text(unit.source_text) != unit.source_hash:
            errors.append("current unit source_hash does not match source_text")
        if structural.source_hash != unit.source_hash:
            errors.append("unit source hash is stale")
    packet_entry = packet_units.get(structural.unit_id)
    if packet_entry is None:
        errors.append("unit belongs to a different packet")
    else:
        packet_unit = packet_entry["unit"]
        if packet_unit.get("page") != structural.page:
            errors.append("packet page is stale")
        if packet_entry.get("source_hash") != structural.source_hash:
            errors.append("packet unit source hash is stale")
        if packet_entry.get("page_image_sha256") != structural.page_image_sha256:
            errors.append("packet page image hash is stale")
    if packet_page_hashes.get(structural.page) != structural.page_image_sha256:
        errors.append("validated packet page image hash is stale")
    for target_id in structural.target_unit_ids:
        target = units_by_id.get(target_id)
        target_packet_entry = packet_units.get(target_id)
        if target is None:
            errors.append(f"unknown target unit {target_id}")
        else:
            if target.page != structural.page:
                errors.append(f"target unit {target_id} is on a different page")
            if sha256_text(target.source_text) != target.source_hash:
                errors.append(f"target unit {target_id} has an invalid current source hash")
        if target_packet_entry is None:
            errors.append(f"target unit {target_id} belongs to a different packet")
        elif target is not None:
            packet_target = target_packet_entry["unit"]
            if packet_target.get("page") != target.page:
                errors.append(f"target unit {target_id} packet page is stale")
            if target_packet_entry.get("source_hash") != target.source_hash:
                errors.append(f"target unit {target_id} packet source hash is stale")
    parent_id = structural.final_values.parent_id
    if "parent_id" in structural.fields and parent_id is not None:
        if parent_id not in units_by_id:
            errors.append(f"unknown final parent unit {parent_id}")
        if parent_id not in packet_units:
            errors.append(f"final parent unit {parent_id} belongs to a different packet")
    bbox = structural.final_values.set_bbox
    if bbox is not None:
        x0, y0, x1, y1 = bbox
        if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
            errors.append("final set_bbox is invalid")
        else:
            try:
                with fitz.open(source) as document:
                    rect = document[structural.page - 1].rect
                if x1 > rect.width or y1 > rect.height:
                    errors.append("final set_bbox extends outside the PDF page")
            except (OSError, ValueError, IndexError) as exc:
                errors.append(f"final set_bbox page is unavailable: {exc}")
    return errors


def _structural_layout_overrides(
    decision: MathStructuralOverrideDecision,
    math_decision: MathReviewDecision,
    units_by_id: Mapping[str, SourceUnit],
) -> list[dict[str, Any]]:
    """Translate the closed sidecar model to the layout importer's safe subset."""

    reason = decision.reason.strip()
    final_values = decision.final_values.model_dump(mode="json", exclude_unset=True)
    if "set_bbox" in final_values and final_values["set_bbox"] is not None:
        final_values["set_bbox"] = list(final_values["set_bbox"])
    if decision.action is MathStructuralAction.IGNORE:
        raw_overrides = [{"unit_id": decision.unit_id, "ignore": True, "reason": reason}]
    elif decision.action is MathStructuralAction.MERGE:
        primary = decision.target_unit_ids[0]
        raw_overrides = [
            {"unit_id": decision.unit_id, "ignore": True, "reason": reason},
            {"unit_id": primary, "reason": reason, **final_values},
        ]
    else:
        # Split proposals may only revise an already-existing source unit.  They
        # cannot manufacture an insert_after record; target IDs are evidence
        # bindings to packet units and are not selectors in layout.yaml.
        raw_overrides = [{"unit_id": decision.unit_id, "reason": reason, **final_values}]

    guarded: list[dict[str, Any]] = []
    for raw_override in raw_overrides:
        unit_id = str(raw_override["unit_id"])
        unit = units_by_id[unit_id]
        reviewed_updates: dict[str, Any] = {
            field: raw_override[field]
            for field in ("source_markdown", "latex", "equation_number")
            if field in raw_override
        }
        reviewed_unit = SourceUnit.model_validate(
            unit.model_copy(update=reviewed_updates).model_dump(mode="python")
        )
        entry = {
            **raw_override,
            "math_review_decision_id": decision.decision_id,
            "candidate_id": math_decision.candidate_id,
            "source_pdf_sha256": decision.source_pdf_sha256,
            "source_hash": unit.source_hash,
            "crop_sha256": math_decision.crop_sha256,
            "page_image_sha256": decision.page_image_sha256,
            "reviewed_against_pdf": True,
        }
        entry["math_review_guard"] = {
            "schema_version": 1,
            "decision_id": decision.decision_id,
            "unit_id": unit.unit_id,
            "page": unit.page,
            "source_pdf_sha256": decision.source_pdf_sha256,
            "source_hash": unit.source_hash,
            "unit_guard_sha256": canonical_math_review_unit_guard_sha256(unit),
            "before_representation_sha256": canonical_math_review_representation_sha256(unit),
            "after_representation_sha256": canonical_math_review_representation_sha256(
                reviewed_unit
            ),
            "crop_sha256": math_decision.crop_sha256,
            "page_image_sha256": decision.page_image_sha256,
            "reviewed_against_pdf": True,
        }
        guarded.append(entry)
    return guarded


def _load_existing_structural_reviews(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    records: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            decision = MathStructuralOverrideDecision.model_validate_json(line)
        except Exception as exc:
            raise ValueError(
                f"Invalid existing structural math review at {path}:{line_number}: {exc}"
            ) from exc
        if decision.decision_id in records:
            raise ValueError(f"Duplicate existing structural decision_id: {decision.decision_id}")
        records[decision.decision_id] = _canonical_decision_sha256(decision)
    return records


def _staged_structural_removal_unit_ids(
    overrides: Sequence[Mapping[str, Any]],
    structural_decision_ids: set[str],
) -> set[str]:
    """Return units already removed by a durable structural decision.

    A later visual pass can legitimately record a new decision for the same
    packet unit.  Re-staging an ``ignore`` transition for an already removed
    unit is not a second state transition, though: it makes the authenticated
    chain impossible to replay once the first transition reaches ``absent``.
    Only hash-validated structural decision IDs are eligible here; an
    unrelated or unguarded layout entry must never suppress a fresh review.
    """

    return {
        unit_id
        for override in overrides
        if override.get("ignore") is True
        and isinstance((unit_id := override.get("unit_id")), str)
        and isinstance((decision_id := override.get("math_review_decision_id")), str)
        and decision_id in structural_decision_ids
    }


def _reject_conflicting_replays(
    incoming: Sequence[MathReviewDecision | MathStructuralOverrideDecision],
    durable_hashes: Mapping[str, str],
    label: str,
) -> None:
    for decision in incoming:
        durable_hash = durable_hashes.get(decision.decision_id)
        if durable_hash is None:
            continue
        if _canonical_decision_sha256(decision) != durable_hash:
            raise ValueError(
                f"Strict {label} decision_id conflict for {decision.decision_id}: "
                "incoming record is not canonically identical to the durable record"
            )


def _serialize_structural_review(
    review: MathStructuralOverrideDecision,
) -> str:
    """Serialize exactly the structural fields that participated in its hash.

    ``exclude_none`` is intentionally not used here.  A declared structural
    field whose final value is null means "clear this value" and is therefore
    part of both the decision semantics and its canonical hash.  Conversely,
    ``exclude_unset`` keeps unrelated defaults out of the durable receipt.
    """

    return review.model_dump_json(exclude_unset=True)


def repair_math_structural_review_ledger(root: Path) -> dict[str, Any]:
    """Restore explicit nulls dropped by the former structural receipt writer.

    Repair is deliberately narrow: the only permitted mutation is adding null
    for fields named by ``fields`` but absent from ``final_values``.  The
    resulting raw payload must reproduce the already-recorded canonical hash
    and pass the complete current model validation before the ledger is
    atomically replaced.  Any other malformed record aborts the whole repair.
    """

    root = Path(root)
    path = root / STRUCTURAL_REVIEWS_PATH
    with project_write_lock(root):
        if not path.is_file():
            return {
                "repaired": True,
                "structural_reviews_path": str(path),
                "record_count": 0,
                "repaired_count": 0,
            }

        original = path.read_text(encoding="utf-8")
        output_lines: list[str] = []
        repaired_count = 0
        decision_ids: set[str] = set()
        for line_number, line in enumerate(original.splitlines(), 1):
            if not line.strip():
                output_lines.append(line)
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Refusing to repair invalid structural math review at "
                    f"{path}:{line_number}: invalid JSON: {exc}"
                ) from exc

            repaired = False
            try:
                decision = MathStructuralOverrideDecision.model_validate(raw)
            except Exception as original_exc:
                if not isinstance(raw, dict):
                    raise ValueError(
                        f"Refusing to repair invalid structural math review at "
                        f"{path}:{line_number}: record is not an object"
                    ) from original_exc
                fields = raw.get("fields")
                final_values = raw.get("final_values")
                recorded_hash = raw.get("canonical_override_sha256")
                if (
                    not isinstance(fields, list)
                    or not all(isinstance(field, str) for field in fields)
                    or len(fields) != len(set(fields))
                    or not isinstance(final_values, dict)
                    or not isinstance(recorded_hash, str)
                ):
                    raise ValueError(
                        f"Refusing to repair invalid structural math review at "
                        f"{path}:{line_number}: {original_exc}"
                    ) from original_exc
                declared = set(fields)
                present = set(final_values)
                missing = declared - present
                if not missing or present - declared:
                    raise ValueError(
                        f"Refusing to repair invalid structural math review at "
                        f"{path}:{line_number}: {original_exc}"
                    ) from original_exc
                candidate = dict(raw)
                candidate_final_values = dict(final_values)
                for field in missing:
                    candidate_final_values[field] = None
                candidate["final_values"] = candidate_final_values
                if canonical_math_structural_override_sha256(candidate) != recorded_hash:
                    raise ValueError(
                        f"Refusing to repair invalid structural math review at "
                        f"{path}:{line_number}: adding declared null fields does not "
                        "match canonical_override_sha256"
                    ) from original_exc
                try:
                    decision = MathStructuralOverrideDecision.model_validate(candidate)
                except Exception as repaired_exc:
                    raise ValueError(
                        f"Refusing to repair invalid structural math review at "
                        f"{path}:{line_number}: null restoration does not produce a "
                        f"valid receipt: {repaired_exc}"
                    ) from repaired_exc
                raw = candidate
                repaired = True

            if decision.decision_id in decision_ids:
                raise ValueError(
                    f"Refusing to repair duplicate structural decision_id at "
                    f"{path}:{line_number}: {decision.decision_id}"
                )
            decision_ids.add(decision.decision_id)
            if repaired:
                output_lines.append(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))
                repaired_count += 1
            else:
                output_lines.append(line)

        if repaired_count:
            repaired_text = "\n".join(output_lines)
            if original.endswith(("\n", "\r")):
                repaired_text += "\n"
            atomic_write_text(path, repaired_text)
        return {
            "repaired": True,
            "structural_reviews_path": str(path),
            "record_count": len(decision_ids),
            "repaired_count": repaired_count,
        }


def import_math_review(
    root: Path,
    input_file: Path,
    confirm_visual_review: bool = False,
    structural_file: Path | None = None,
) -> dict[str, Any]:
    """Import fresh visual decisions as durable layout overrides and receipts."""

    if not confirm_visual_review:
        raise ValueError(
            "Math review import requires confirm_visual_review after comparing the report with the PDF"
        )
    root = Path(root)
    input_file = Path(input_file)
    with project_write_lock(root):
        # Inputs are parsed under the project write lock so a replacement
        # cannot race freshness validation and the authoritative writes.
        decisions = _read_decisions(input_file)
        structural_sidecar = (
            _read_structural_sidecar(Path(structural_file)) if structural_file is not None else None
        )
        reviews_path = root / REVIEWS_PATH
        structural_reviews_path = root / STRUCTURAL_REVIEWS_PATH
        existing_reviews = _load_existing_reviews(reviews_path)
        existing_structural_reviews = _load_existing_structural_reviews(structural_reviews_path)
        _reject_conflicting_replays(decisions, existing_reviews, "math review")
        structural_overrides = (
            list(structural_sidecar.overrides) if structural_sidecar is not None else []
        )
        if structural_sidecar is not None:
            _reject_conflicting_replays(
                structural_overrides,
                existing_structural_reviews,
                "structural math review",
            )
        pending_decisions = [
            decision for decision in decisions if decision.decision_id not in existing_reviews
        ]
        pending_structural = [
            decision
            for decision in structural_overrides
            if decision.decision_id not in existing_structural_reviews
        ]
        replay_outcomes = [
            {"decision_id": decision.decision_id, "status": "already-recorded"}
            for decision in decisions
            if decision.decision_id in existing_reviews
        ]
        replay_outcomes.extend(
            {
                "decision_id": decision.decision_id,
                "status": "structural-already-recorded",
            }
            for decision in structural_overrides
            if decision.decision_id in existing_structural_reviews
        )
        layout_path = root / LAYOUT_PATH
        if not pending_decisions and not pending_structural:
            return {
                "imported": True,
                "reviews_path": str(reviews_path),
                "layout_path": str(layout_path),
                "decision_count": len(decisions),
                "recorded_count": 0,
                "override_count": 0,
                "structural_count": 0,
                "structural_override_count": 0,
                "structural_layout_override_count": 0,
                "structural_reviews_path": str(structural_reviews_path),
                "rejected_count": 0,
                "unresolved_count": 0,
                "outcomes": replay_outcomes,
                "units_path_untouched": True,
            }
        config = load_project(root)
        source = config.source(root)
        source_sha256 = sha256_file(source)
        if source_sha256 != config.source_sha256:
            raise ValueError("Source PDF hash changed after project initialization")
        units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
        unit_counts: dict[str, int] = defaultdict(int)
        for ledger_unit in units:
            unit_counts[ledger_unit.unit_id] += 1
        duplicate_unit_ids = sorted(unit_id for unit_id, count in unit_counts.items() if count > 1)
        if duplicate_unit_ids:
            raise ValueError(
                "Math review import requires unique current SourceUnit IDs; duplicates: "
                + ", ".join(duplicate_unit_ids)
            )
        units_by_id = {unit.unit_id: unit for unit in units}
        # A fully packet-bound manual/rejected import is independent of the
        # remote-candidate ledger, just like manual packet construction.
        candidates = (
            _read_candidates(root)
            if any(decision.packet_id is None for decision in pending_decisions)
            else []
        )
        candidates_by_id: dict[str, MathCandidate] = {}
        for recorded_candidate in candidates:
            if recorded_candidate.candidate_id in candidates_by_id:
                raise ValueError(f"Duplicate math candidate ID: {recorded_candidate.candidate_id}")
            candidates_by_id[recorded_candidate.candidate_id] = recorded_candidate

        validated: list[tuple[MathReviewDecision, MathCandidate | None, SourceUnit, str]] = []
        stale: list[str] = []
        for decision in pending_decisions:
            selected_unit = units_by_id.get(decision.unit_id)
            if selected_unit is None:
                stale.append(f"{decision.decision_id}: unknown unit {decision.unit_id}")
                continue
            selected_candidate: MathCandidate | None = None
            if decision.candidate_id is not None:
                selected_candidate = candidates_by_id.get(decision.candidate_id)
                if selected_candidate is None:
                    stale.append(
                        f"{decision.decision_id}: unknown candidate {decision.candidate_id}"
                    )
                    continue
            else:
                # Manual/rejected decisions may bind directly to a crop.  If
                # that crop is one of the candidate records, validate its
                # response hash as well; otherwise the unit asset hash check
                # below remains authoritative.
                if decision.packet_id is None:
                    matches = [
                        item
                        for item in candidates
                        if item.unit_id == decision.unit_id
                        and item.crop_sha256 == decision.crop_sha256
                    ]
                    if len(matches) == 1:
                        selected_candidate = matches[0]
            packet_bound = decision.packet_id is not None
            packet_errors: list[str] = []
            if packet_bound:
                try:
                    _load_math_decision_packet(
                        root,
                        source,
                        source_sha256,
                        decision,
                        selected_unit,
                    )
                except (OSError, ValueError) as exc:
                    packet_errors.append(str(exc))
            errors = packet_errors + _freshness_errors(
                root,
                source,
                source_sha256,
                decision,
                selected_unit,
                selected_candidate,
                packet_bound=packet_bound,
            )
            if errors:
                stale.append(f"{decision.decision_id}: " + "; ".join(errors))
                continue
            disposition = _value(decision.disposition)
            if disposition in {
                MathReviewDisposition.ACCEPTED.value,
                MathReviewDisposition.CORRECTED.value,
                MathReviewDisposition.MANUAL.value,
            }:
                unresolved = list(decision.structural_issues) + list(decision.uncertainties)
                if selected_candidate is not None:
                    unresolved.extend(selected_candidate.uncertainties)
                    if selected_candidate.needs_second_pass:
                        unresolved.append("candidate requires a second pass")
                    if _value(selected_candidate.classification) in {
                        MathCandidateClassification.MIXED.value,
                        MathCandidateClassification.NOT_MATH.value,
                        MathCandidateClassification.NEEDS_SPLIT.value,
                    }:
                        unresolved.append(
                            "candidate classification "
                            f"{_value(selected_candidate.classification)} requires adjudication"
                        )
                    if (
                        _value(selected_candidate.classification)
                        == MathCandidateClassification.DISPLAY.value
                        and selected_unit.kind is not UnitKind.EQUATION
                    ):
                        unresolved.append("display candidate does not match the unit structure")
                    if (
                        _value(selected_candidate.classification)
                        == MathCandidateClassification.INLINE.value
                        and selected_unit.kind is UnitKind.EQUATION
                    ):
                        unresolved.append("inline candidate does not match the unit structure")
                if unresolved:
                    validated.append(
                        (
                            decision,
                            selected_candidate,
                            selected_unit,
                            "unresolved: " + "; ".join(unresolved),
                        )
                    )
                    continue
                try:
                    _candidate_representation(decision, selected_candidate, selected_unit)
                except ValueError as exc:
                    stale.append(f"{decision.decision_id}: {exc}")
                    continue
                validated.append((decision, selected_candidate, selected_unit, "ready"))
            else:
                validated.append((decision, selected_candidate, selected_unit, "rejected"))
        if stale:
            raise ValueError("Math review evidence is stale or invalid: " + " | ".join(stale))

        structural_decisions: list[MathStructuralOverrideDecision] = []
        packet_units: dict[str, dict[str, Any]] = {}
        decisions_by_id = {decision.decision_id: decision for decision in decisions}
        if pending_structural:
            if structural_sidecar is None:
                raise AssertionError("Pending structural reviews require a sidecar")
            _, packet_units, packet_page_hashes = _load_structural_packet(
                root,
                source,
                source_sha256,
                structural_sidecar,
                units_by_id,
            )
            structural_stale: list[str] = []
            seen_structural_units: set[str] = set()
            for structural in pending_structural:
                if structural.unit_id in seen_structural_units:
                    structural_stale.append(
                        f"{structural.decision_id}: duplicate structural unit ID "
                        f"{structural.unit_id}"
                    )
                    continue
                seen_structural_units.add(structural.unit_id)
                errors = _structural_binding_errors(
                    source,
                    source_sha256,
                    structural,
                    decisions_by_id.get(structural.decision_id),
                    units_by_id.get(structural.unit_id),
                    packet_units,
                    packet_page_hashes,
                    units_by_id,
                )
                if errors:
                    structural_stale.append(f"{structural.decision_id}: " + "; ".join(errors))
                else:
                    structural_decisions.append(structural)
            if structural_stale:
                raise ValueError(
                    "Structural math review evidence is stale or invalid: "
                    + " | ".join(structural_stale)
                )
        if sha256_file(source) != source_sha256:
            raise ValueError("Source PDF changed while math review evidence was validated")

        layout = _load_layout(layout_path)
        existing_overrides = list(layout.get("overrides", []))
        staged_structural_removals = _staged_structural_removal_unit_ids(
            existing_overrides,
            set(existing_structural_reviews),
        )
        new_reviews: list[MathReviewDecision] = []
        new_overrides: list[dict[str, Any]] = []
        outcomes: list[dict[str, Any]] = list(replay_outcomes)
        for decision, selected_candidate, unit, state in validated:
            new_reviews.append(decision)
            disposition = _value(decision.disposition)
            if disposition == MathReviewDisposition.REJECTED.value:
                outcomes.append({"decision_id": decision.decision_id, "status": "rejected"})
                continue
            if state != "ready":
                outcomes.append(
                    {"decision_id": decision.decision_id, "status": "unresolved", "reason": state}
                )
                continue
            new_overrides.append(
                _decision_override(decision, selected_candidate, unit, source_sha256)
            )
            outcomes.append(
                {
                    "decision_id": decision.decision_id,
                    "status": "overridden",
                    "unit_id": unit.unit_id,
                }
            )

        new_structural_reviews: list[MathStructuralOverrideDecision] = []
        new_structural_overrides: list[dict[str, Any]] = []
        for structural in structural_decisions:
            new_structural_reviews.append(structural)
            converted = _structural_layout_overrides(
                structural,
                decisions_by_id[structural.decision_id],
                units_by_id,
            )
            removal_already_staged = (
                structural.action in {MathStructuralAction.IGNORE, MathStructuralAction.MERGE}
                and structural.unit_id in staged_structural_removals
            )
            if removal_already_staged:
                converted = [
                    override
                    for override in converted
                    if not (
                        override.get("unit_id") == structural.unit_id
                        and override.get("ignore") is True
                    )
                ]
            elif structural.action in {
                MathStructuralAction.IGNORE,
                MathStructuralAction.MERGE,
            }:
                staged_structural_removals.add(structural.unit_id)
            new_structural_overrides.extend(converted)
            outcomes.append(
                {
                    "decision_id": structural.decision_id,
                    "status": "structural-overridden",
                    "unit_id": structural.unit_id,
                    "action": _value(structural.action),
                    "removal_already_staged": removal_already_staged,
                }
            )

        layout["overrides"] = existing_overrides + new_overrides + new_structural_overrides
        layout_snapshot = snapshot_files([layout_path, reviews_path, structural_reviews_path])
        try:
            # A rejected or unresolved review is a receipt only.  Do not
            # create/reformat layout.yaml when there is no proposed override.
            if new_overrides or new_structural_overrides:
                write_yaml(layout_path, layout)
            if new_reviews:
                reviews_path.parent.mkdir(parents=True, exist_ok=True)
                existing_text = (
                    reviews_path.read_text(encoding="utf-8") if reviews_path.is_file() else ""
                )
                addition = "".join(
                    review.model_dump_json(exclude_none=True) + "\n" for review in new_reviews
                )
                atomic_write_text(reviews_path, existing_text + addition)
            if new_structural_reviews:
                structural_reviews_path.parent.mkdir(parents=True, exist_ok=True)
                existing_structural_text = (
                    structural_reviews_path.read_text(encoding="utf-8")
                    if structural_reviews_path.is_file()
                    else ""
                )
                structural_addition = "".join(
                    _serialize_structural_review(review) + "\n" for review in new_structural_reviews
                )
                atomic_write_text(
                    structural_reviews_path,
                    existing_structural_text + structural_addition,
                )
        except Exception:
            restore_files(layout_snapshot)
            raise
        return {
            "imported": True,
            "reviews_path": str(reviews_path),
            "layout_path": str(layout_path),
            "decision_count": len(decisions),
            "recorded_count": len(new_reviews),
            "override_count": len(new_overrides),
            "structural_count": len(new_structural_reviews),
            "structural_override_count": len(new_structural_reviews),
            "structural_layout_override_count": len(new_structural_overrides),
            "structural_reviews_path": str(structural_reviews_path),
            "rejected_count": sum(1 for item in outcomes if item["status"] == "rejected"),
            "unresolved_count": sum(1 for item in outcomes if item["status"] == "unresolved"),
            "outcomes": outcomes,
            "units_path_untouched": True,
        }
