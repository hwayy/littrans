"""Build immutable, page-complete packets for visual mathematics review.

The packet builder is intentionally one-way: it snapshots source evidence and
creates an empty result area, but never manufactures review decisions or
changes extraction/verification ledgers.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import fitz

from littrans.extractor import parse_page_spec
from littrans.models import MathCandidate, SemanticStatus, SourceUnit, UnitKind
from littrans.storage import (
    atomic_write_bytes,
    atomic_write_text,
    load_project,
    read_jsonl,
    sha256_file,
    sha256_text,
)

DEFAULT_OUTPUT_ROOT = Path(".littrans") / "work" / "math-review-packets"
PAGE_IMAGE_DIR = Path("derived") / "verification"
CANDIDATES_PATH = Path("evidence") / "math" / "candidates.jsonl"
UNITS_PATH = Path("derived") / "units.jsonl"
VERIFICATION_PATH = Path("derived") / "verification.json"

_MATH_MARKER_RE = re.compile(r"(?<!\\)(?:\$|\\\(|\\\[)")
_SAFE_SUFFIX_RE = re.compile(r"[^A-Za-z0-9.]+")
_SAFE_STEM_RE = re.compile(r"[^A-Za-z0-9._-]+")

MANUAL_REVIEW_CROP_SCALE = 3.0
MANUAL_REVIEW_CROP_PADDING_PT = 4.0


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value)).strip().lower()


def _needs_math_review(unit: SourceUnit) -> bool:
    """Match candidate-generation semantics for inline and display math."""

    status = _enum_value(unit.math_status) if unit.math_status is not None else ""
    if status == _enum_value(SemanticStatus.VERIFIED):
        return False
    if unit.kind is UnitKind.EQUATION:
        return True
    if status:
        return status == _enum_value(SemanticStatus.UNVERIFIED)
    return bool(_MATH_MARKER_RE.search(unit.source_markdown or ""))


def _inside(root: Path, path: Path, label: str) -> Path:
    root_resolved = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"{label} must remain inside the project: {path}") from exc
    return resolved


def _project_asset(root: Path, value: str, label: str) -> Path:
    path = Path(value)
    candidate = path if path.is_absolute() else root / path
    return _inside(root, candidate, label)


def _project_relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _read_candidates(root: Path) -> list[MathCandidate]:
    return read_jsonl(root / CANDIDATES_PATH, MathCandidate)


def _load_verification(root: Path) -> tuple[dict[str, Any], str | None]:
    path = root / VERIFICATION_PATH
    if not path.exists():
        return {}, None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid verification evidence: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Verification evidence must contain a JSON object: {path}")
    return value, sha256_file(path)


def _overlap_relations(payload: Mapping[str, Any], pages: set[int]) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    for field in ("errors", "overlap_groups"):
        raw = payload.get(field, [])
        if not isinstance(raw, list):
            continue
        for value in raw:
            if not isinstance(value, dict):
                continue
            code = str(value.get("code", "")).casefold()
            if field == "errors" and "overlap" not in code:
                continue
            page = value.get("page")
            if isinstance(page, int) and page in pages:
                relations.append({"verification_field": field, "relation": value})
    relations.sort(key=_canonical_bytes)
    return relations


def _page_runs(pages: Sequence[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for page in pages:
        if not runs or page != runs[-1][-1] + 1:
            runs.append([page])
        else:
            runs[-1].append(page)
    return runs


def _partition_run(
    pages: Sequence[int], counts: Mapping[int, int], target_units: int, max_units: int
) -> list[list[int]]:
    """Partition one consecutive run without ever splitting a page."""

    packets: list[list[int]] = []
    current: list[int] = []
    current_count = 0
    for page in pages:
        count = counts.get(page, 0)
        if count > max_units:
            if current:
                packets.append(current)
                current = []
                current_count = 0
            packets.append([page])
            continue
        if current:
            combined = current_count + count
            past_target = current_count >= target_units
            exceeds_limit = combined > max_units
            moves_away_from_target = count > 0 and abs(current_count - target_units) < abs(
                combined - target_units
            )
            if past_target or exceeds_limit or moves_away_from_target:
                packets.append(current)
                current = []
                current_count = 0
        current.append(page)
        current_count += count
    if current:
        packets.append(current)
    return packets


def _asset_bytes(path: Path, expected_sha256: str | None, label: str) -> tuple[bytes, str]:
    if not path.is_file():
        raise ValueError(f"Missing {label}: {path}")
    content = path.read_bytes()
    actual = hashlib.sha256(content).hexdigest()
    if expected_sha256 is not None and actual != expected_sha256:
        raise ValueError(f"Stale {label} hash for {path}: expected {expected_sha256}, got {actual}")
    return content, actual


def _snapshot_name(category: str, path: Path, digest: str) -> str:
    suffix = _SAFE_SUFFIX_RE.sub("", path.suffix.lower())[:12]
    extension = suffix if suffix.startswith(".") else ""
    return f"assets/{category}/{digest}{extension}"


def _candidate_entry(
    root: Path,
    candidate: MathCandidate,
    unit: SourceUnit,
    source_pdf_sha256: str,
    planned_assets: dict[str, bytes],
) -> dict[str, Any]:
    stale: list[str] = []
    if candidate.source_pdf_sha256 != source_pdf_sha256:
        stale.append("source PDF hash")
    if candidate.page != unit.page:
        stale.append("page")
    if candidate.source_hash != unit.source_hash:
        stale.append("source hash")
    if stale:
        raise ValueError(
            f"Stale math candidate {candidate.candidate_id} for {unit.unit_id}: " + ", ".join(stale)
        )

    crop = _project_asset(root, candidate.crop_path, "candidate crop path")
    crop_bytes, crop_sha256 = _asset_bytes(
        crop, candidate.crop_sha256, f"candidate crop for {candidate.candidate_id}"
    )
    crop_snapshot = _snapshot_name("crops", crop, crop_sha256)
    planned_assets.setdefault(crop_snapshot, crop_bytes)

    response = _project_asset(root, candidate.raw_response_path, "candidate response path")
    response_bytes, response_sha256 = _asset_bytes(
        response,
        candidate.response_sha256,
        f"candidate raw response for {candidate.candidate_id}",
    )
    response_snapshot = _snapshot_name("responses", response, response_sha256)
    planned_assets.setdefault(response_snapshot, response_bytes)

    serialized = candidate.model_dump(mode="json")
    return {
        "candidate": serialized,
        "candidate_record_sha256": _canonical_sha256(serialized),
        "bindings": {
            "unit_id": unit.unit_id,
            "source_pdf_sha256": source_pdf_sha256,
            "source_hash": unit.source_hash,
            "crop": {
                "project_path": _project_relative(root, crop),
                "packet_path": crop_snapshot,
                "sha256": crop_sha256,
            },
            "raw_response": {
                "project_path": _project_relative(root, response),
                "packet_path": response_snapshot,
                "sha256": response_sha256,
            },
        },
    }


def _unit_assets(
    root: Path, unit: SourceUnit, planned_assets: dict[str, bytes]
) -> list[dict[str, str]]:
    assets: list[dict[str, str]] = []
    for asset in unit.asset_refs:
        path = _project_asset(root, asset.path, f"asset path for {unit.unit_id}")
        content, digest = _asset_bytes(path, None, f"unit asset for {unit.unit_id}")
        snapshot = _snapshot_name("unit-assets", path, digest)
        planned_assets.setdefault(snapshot, content)
        assets.append(
            {
                "kind": asset.kind,
                "project_path": _project_relative(root, path),
                "packet_path": snapshot,
                "sha256": digest,
            }
        )
    return assets


def _render_full_page(document: fitz.Document, page: int) -> tuple[bytes, str]:
    """Render one source-PDF page with a fixed, hashable rendering contract."""

    pixmap = document.load_page(page - 1).get_pixmap(
        matrix=fitz.Matrix(2.0, 2.0),
        alpha=False,
    )
    content = pixmap.tobytes("png")
    return content, hashlib.sha256(content).hexdigest()


def _manual_review_crop(
    document: fitz.Document, unit: SourceUnit
) -> tuple[bytes, str, tuple[float, float, float, float]]:
    """Render a deterministic, lightly padded 3x crop from the unit's PDF bbox."""

    pdf_page = document.load_page(unit.page - 1)
    x0, y0, x1, y1 = unit.bbox
    bbox = fitz.Rect(x0, y0, x1, y1)
    if bbox.is_empty or bbox.is_infinite:
        raise ValueError(f"Invalid review crop bbox for {unit.unit_id}: {unit.bbox}")
    clip = fitz.Rect(
        bbox.x0 - MANUAL_REVIEW_CROP_PADDING_PT,
        bbox.y0 - MANUAL_REVIEW_CROP_PADDING_PT,
        bbox.x1 + MANUAL_REVIEW_CROP_PADDING_PT,
        bbox.y1 + MANUAL_REVIEW_CROP_PADDING_PT,
    ) & pdf_page.rect
    if clip.is_empty or clip.is_infinite:
        raise ValueError(f"Review crop bbox is outside PDF page for {unit.unit_id}: {unit.bbox}")
    pixmap = pdf_page.get_pixmap(
        matrix=fitz.Matrix(MANUAL_REVIEW_CROP_SCALE, MANUAL_REVIEW_CROP_SCALE),
        clip=clip,
        alpha=False,
    )
    content = pixmap.tobytes("png")
    return content, hashlib.sha256(content).hexdigest(), tuple(clip)


def _review_crop_snapshot(unit: SourceUnit, digest: str) -> str:
    safe_unit_id = (_SAFE_STEM_RE.sub("-", unit.unit_id).strip(".-") or "unit")[:80]
    return f"assets/review-crops/{safe_unit_id}-{digest}.png"


def _write_packet(
    packet_dir: Path,
    manifest: dict[str, Any],
    planned_assets: Mapping[str, bytes],
    *,
    empty_result: bool = False,
) -> None:
    manifest_path = packet_dir / "packet" / "manifest.json"
    result_readme = packet_dir / "result" / "README.md"
    readme = (
        "# Review result directory\n\n"
        "This directory is intentionally empty of decisions. A visual reviewer must create "
        "the result and bind every decision to the hashes in `../packet/manifest.json`.\n"
    )
    if packet_dir.exists():
        if not manifest_path.is_file():
            raise ValueError(f"Existing immutable packet is incomplete: {packet_dir}")
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Existing immutable packet manifest is invalid: {manifest_path}"
            ) from exc
        if existing != manifest:
            raise ValueError(
                f"Existing immutable packet conflicts with planned content: {packet_dir}"
            )
        for relative, content in planned_assets.items():
            path = packet_dir / "packet" / relative
            if not path.is_file() or path.read_bytes() != content:
                raise ValueError(f"Existing immutable packet asset is missing or changed: {path}")
        if empty_result:
            if not result_readme.parent.is_dir():
                raise ValueError(
                    f"Existing immutable packet result directory is missing: {packet_dir}"
                )
        elif not result_readme.is_file() or result_readme.read_text(encoding="utf-8") != readme:
            raise ValueError(
                f"Existing immutable packet result instructions changed: {result_readme}"
            )
        return

    for relative, content in planned_assets.items():
        atomic_write_bytes(packet_dir / "packet" / relative, content)
    atomic_write_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    if empty_result:
        result_readme.parent.mkdir(parents=True, exist_ok=True)
    else:
        atomic_write_text(result_readme, readme)


def build_math_review_packets(
    root: Path,
    page_spec: str = "all",
    target_units: int = 40,
    max_units: int = 60,
    output_root: Path | None = None,
    manual_only: bool = False,
    require_candidates: bool = False,
    include_unit_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build immutable visual-review packets over complete consecutive pages.

    Only unverified inline/display mathematics contributes by default. Exact
    ``include_unit_ids`` may supplement a manual-only packet with other current
    units. No page is split. A page containing more than ``max_units`` is
    emitted as a single, explicitly marked density exception.
    """

    project_root = Path(root).resolve()
    if not project_root.is_dir():
        raise ValueError(f"Project root does not exist: {project_root}")
    if target_units < 1 or max_units < 1 or target_units > max_units:
        raise ValueError("target_units and max_units must satisfy 1 <= target_units <= max_units")
    requested_unit_ids: set[str] | None = None
    if include_unit_ids is not None:
        if isinstance(include_unit_ids, (str, bytes)):
            raise ValueError("include_unit_ids must be a sequence of exact unit IDs")
        if any(not isinstance(value, str) for value in include_unit_ids):
            raise ValueError("include_unit_ids must contain only strings")
        normalized_ids = [value.strip() for value in include_unit_ids]
        if any(not value for value in normalized_ids):
            raise ValueError("include_unit_ids cannot contain empty values")
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("include_unit_ids cannot contain duplicates")
        if normalized_ids and not manual_only:
            raise ValueError("include_unit_ids requires manual_only visual review packets")
        requested_unit_ids = set(normalized_ids) or None

    config = load_project(project_root)
    source = (
        _project_asset(project_root, config.source_path, "source PDF path")
        if manual_only
        else config.source(project_root)
    )
    if not source.is_file():
        raise ValueError(f"Source PDF is missing: {source}")
    source_pdf_sha256 = sha256_file(source)
    if source_pdf_sha256 != config.source_sha256:
        raise ValueError("Source PDF hash changed after project initialization")

    with fitz.open(source) as document:
        pages = parse_page_spec(page_spec, document.page_count)
    selected_pages = set(pages)
    units = read_jsonl(project_root / UNITS_PATH, SourceUnit)
    if requested_unit_ids is not None:
        known_ids = {unit.unit_id for unit in units}
        unknown_ids = sorted(requested_unit_ids - known_ids)
        if unknown_ids:
            raise ValueError("unknown include_unit_ids: " + ", ".join(unknown_ids))
        duplicated_source_ids = sorted(
            unit_id
            for unit_id in requested_unit_ids
            if sum(unit.unit_id == unit_id for unit in units) != 1
        )
        if duplicated_source_ids:
            raise ValueError(
                "include_unit_ids do not uniquely identify current SourceUnits: "
                + ", ".join(duplicated_source_ids)
            )
        page_excluded_ids = sorted(
            unit.unit_id
            for unit in units
            if unit.unit_id in requested_unit_ids and unit.page not in selected_pages
        )
        if page_excluded_ids:
            raise ValueError(
                "include_unit_ids are outside the selected pages: "
                + ", ".join(page_excluded_ids)
            )
    review_units = [
        unit
        for unit in units
        if unit.page in selected_pages and _needs_math_review(unit)
    ]
    selected_unit_ids = {unit.unit_id for unit in review_units}
    review_units.extend(
        unit
        for unit in units
        if unit.unit_id in (requested_unit_ids or set())
        and unit.unit_id not in selected_unit_ids
    )
    review_units.sort(key=lambda item: (item.page, item.bbox[1], item.bbox[0], item.unit_id))
    explicit_unit_ids = [
        unit.unit_id
        for unit in review_units
        if unit.unit_id in (requested_unit_ids or set())
    ]
    if manual_only:
        seen_unit_ids: set[str] = set()
        for unit in review_units:
            if unit.unit_id in seen_unit_ids:
                raise ValueError(f"Duplicate selected SourceUnit ID: {unit.unit_id}")
            seen_unit_ids.add(unit.unit_id)
            if unit.source_hash != sha256_text(unit.source_text):
                raise ValueError(f"Stale SourceUnit source_hash: {unit.unit_id}")
    if not review_units:
        return {
            "output_root": str(
                _resolve_output_root(project_root, output_root).relative_to(project_root)
            ).replace("\\", "/"),
            "source_pdf_sha256": source_pdf_sha256,
            "packet_count": 0,
            "math_unit_count": 0,
            "packets": [],
        }

    # Manual-only packets are an independent visual review surface.  Do not
    # even parse the candidate ledger: a stale/malformed remote response must
    # neither block the packet nor leak a first reviewer's conclusion into it.
    candidates = [] if manual_only else _read_candidates(project_root)
    seen_candidate_ids: set[str] = set()
    candidates_by_unit: dict[str, list[MathCandidate]] = defaultdict(list)
    selected_unit_ids = {unit.unit_id for unit in review_units}
    for candidate in candidates:
        if candidate.unit_id not in selected_unit_ids:
            continue
        if candidate.candidate_id in seen_candidate_ids:
            raise ValueError(f"Duplicate math candidate ID: {candidate.candidate_id}")
        seen_candidate_ids.add(candidate.candidate_id)
        candidates_by_unit[candidate.unit_id].append(candidate)
    for values in candidates_by_unit.values():
        values.sort(key=lambda item: (item.pass_index, item.candidate_id))
    if require_candidates and not manual_only:
        missing_candidates = sorted(selected_unit_ids - candidates_by_unit.keys())
        if missing_candidates:
            raise ValueError(
                "Math candidates are required for every selected unit; missing: "
                + ", ".join(missing_candidates)
            )

    units_by_page: dict[int, list[SourceUnit]] = defaultdict(list)
    for unit in review_units:
        units_by_page[unit.page].append(unit)
    counts = {page: len(values) for page, values in units_by_page.items()}
    review_pages = set(counts)
    relevant_runs: list[list[int]] = []
    for run in _page_runs(pages):
        indices = [index for index, page in enumerate(run) if page in review_pages]
        if indices:
            relevant_runs.append(run[min(indices) : max(indices) + 1])
    packet_pages = [
        packet
        for run in relevant_runs
        for packet in _partition_run(run, counts, target_units, max_units)
    ]

    verification, verification_sha256 = _load_verification(project_root)
    destination_root = _resolve_output_root(project_root, output_root)
    summaries: list[dict[str, Any]] = []
    manual_document = fitz.open(source) if manual_only else None
    try:
        packet_iterator = packet_pages
        for page_group in packet_iterator:
            summaries.append(
                _build_one_packet(
                    project_root=project_root,
                    source=source,
                    source_pdf_sha256=source_pdf_sha256,
                    config=config,
                    page_group=page_group,
                    units_by_page=units_by_page,
                    candidates_by_unit=candidates_by_unit,
                    verification=verification,
                    verification_sha256=verification_sha256,
                    destination_root=destination_root,
                    target_units=target_units,
                    max_units=max_units,
                    manual_only=manual_only,
                    manual_document=manual_document,
                    explicit_unit_ids=set(explicit_unit_ids),
                )
            )
    finally:
        if manual_document is not None:
            manual_document.close()

    return {
        "output_root": _project_relative(project_root, destination_root),
        "source_pdf_sha256": source_pdf_sha256,
        "packet_count": len(summaries),
        "math_unit_count": len(review_units),
        "packets": summaries,
    }


def _build_one_packet(
    *,
    project_root: Path,
    source: Path,
    source_pdf_sha256: str,
    config: Any,
    page_group: list[int],
    units_by_page: Mapping[int, list[SourceUnit]],
    candidates_by_unit: Mapping[str, list[MathCandidate]],
    verification: Mapping[str, Any],
    verification_sha256: str | None,
    destination_root: Path,
    target_units: int,
    max_units: int,
    manual_only: bool,
    manual_document: fitz.Document | None,
    explicit_unit_ids: set[str],
) -> dict[str, Any]:
    planned_assets: dict[str, bytes] = {}
    page_evidence: list[dict[str, Any]] = []
    for page in page_group:
        if manual_only:
            if manual_document is None:
                raise AssertionError("Manual packet rendering requires an open source PDF")
            content, digest = _render_full_page(manual_document, page)
            image_name = Path(f"page-{page:04d}.png")
            snapshot = _snapshot_name("pages", image_name, digest)
            image_binding: dict[str, Any] = {
                "source_pdf_page": page,
                "packet_path": snapshot,
                "sha256": digest,
                "render": {
                    "engine": "PyMuPDF",
                    "matrix": [2.0, 2.0],
                    "alpha": False,
                },
            }
        else:
            image = _project_asset(
                project_root,
                (PAGE_IMAGE_DIR / f"page-{page:04d}.png").as_posix(),
                f"page image path for page {page}",
            )
            content, digest = _asset_bytes(image, None, f"page image for page {page}")
            snapshot = _snapshot_name("pages", image, digest)
            image_binding = {
                "project_path": _project_relative(project_root, image),
                "packet_path": snapshot,
                "sha256": digest,
            }
        planned_assets.setdefault(snapshot, content)
        page_evidence.append(
            {
                "page": page,
                "image": image_binding,
                "review_unit_ids": [unit.unit_id for unit in units_by_page.get(page, [])],
            }
        )

    packet_units = [unit for page in page_group for unit in units_by_page.get(page, [])]
    unit_entries: list[dict[str, Any]] = []
    for unit in packet_units:
        serialized = unit.model_dump(mode="json")
        entry: dict[str, Any] = {
            "unit": serialized,
            "source_hash": unit.source_hash,
            "unit_record_sha256": _canonical_sha256(serialized),
            "assets": _unit_assets(project_root, unit, planned_assets),
            "candidates": [
                _candidate_entry(
                    project_root,
                    candidate,
                    unit,
                    source_pdf_sha256,
                    planned_assets,
                )
                for candidate in candidates_by_unit.get(unit.unit_id, [])
            ],
        }
        if manual_only:
            if manual_document is None:
                raise AssertionError("Manual packet rendering requires an open source PDF")
            crop_content, crop_sha256, clip_bbox = _manual_review_crop(
                manual_document, unit
            )
            crop_snapshot = _review_crop_snapshot(unit, crop_sha256)
            planned_assets.setdefault(crop_snapshot, crop_content)
            entry["review_crop"] = {
                "packet_path": crop_snapshot,
                "sha256": crop_sha256,
                "source_pdf_page": unit.page,
                "unit_bbox": list(unit.bbox),
                "clip_bbox": list(clip_bbox),
                "render": {
                    "engine": "PyMuPDF",
                    "matrix": [
                        MANUAL_REVIEW_CROP_SCALE,
                        MANUAL_REVIEW_CROP_SCALE,
                    ],
                    "padding_points": MANUAL_REVIEW_CROP_PADDING_PT,
                    "alpha": False,
                },
            }
        unit_entries.append(entry)

    page_set = set(page_group)
    content_payload: dict[str, Any] = {
        "schema_version": 1,
        "kind": "immutable-math-visual-review-packet",
        "source_pdf": {
            "configured_path": config.source_path,
            "sha256": source_pdf_sha256,
        },
        "page_range": {"start": page_group[0], "end": page_group[-1]},
        "pages": page_evidence,
        "math_unit_count": len(packet_units),
        "unit_sequence": [unit.unit_id for unit in packet_units],
        "units": unit_entries,
        "verification": {
            "project_path": VERIFICATION_PATH.as_posix() if verification_sha256 else None,
            "sha256": verification_sha256,
            "overlap_relations": _overlap_relations(verification, page_set),
        },
        "limits": {
            "target_units": target_units,
            "max_units": max_units,
            "single_page_density_exception": (
                len(page_group) == 1 and len(packet_units) > max_units
            ),
        },
        "blockers": [],
        "result": {
            "directory": "../result",
            "decisions_generated": False,
        },
    }
    if manual_only:
        content_payload["review_mode"] = "manual-only"
        content_payload["source_pdf"]["project_path"] = _project_relative(project_root, source)
        packet_explicit_unit_ids = [
            unit.unit_id for unit in packet_units if unit.unit_id in explicit_unit_ids
        ]
        if packet_explicit_unit_ids:
            content_payload["explicit_unit_ids"] = packet_explicit_unit_ids
    packet_sha256 = _canonical_sha256(content_payload)
    packet_id = f"math-review-{packet_sha256[:20]}"
    manifest_core = {
        **content_payload,
        "packet_id": packet_id,
        "packet_sha256": packet_sha256,
    }
    manifest = {
        **manifest_core,
        "manifest_sha256": _canonical_sha256(manifest_core),
    }
    packet_dir = destination_root / packet_id
    _write_packet(
        packet_dir,
        manifest,
        planned_assets,
        empty_result=manual_only,
    )
    return {
        "packet_id": packet_id,
        "page_start": page_group[0],
        "page_end": page_group[-1],
        "pages": page_group,
        "math_unit_count": len(packet_units),
        "density_exception": bool(manifest["limits"]["single_page_density_exception"]),
        "packet_sha256": packet_sha256,
        "manifest_sha256": manifest["manifest_sha256"],
        "manifest_path": _project_relative(project_root, packet_dir / "packet" / "manifest.json"),
        "result_path": _project_relative(project_root, packet_dir / "result"),
    }


def _resolve_output_root(root: Path, output_root: Path | None) -> Path:
    value = DEFAULT_OUTPUT_ROOT if output_root is None else Path(output_root)
    if value.is_absolute():
        resolved = value.resolve()
    else:
        resolved = (root / value).resolve()
    return _inside(root, resolved, "math packet output root")
