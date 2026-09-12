"""Build exact-ID packets for the 60-unit mathematics review pilot."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pymupdf as fitz

from littrans.models import MathCandidate, SourceUnit
from littrans.storage import (
    atomic_write_bytes,
    atomic_write_text,
    load_project,
    read_jsonl,
    sha256_file,
    sha256_text,
)

PILOT_MANIFEST_PATH = Path("evidence/math/pilot-60-manifest.json")
CANDIDATES_PATH = Path("evidence/math/candidates.jsonl")
UNITS_PATH = Path("derived/units.jsonl")
DEFAULT_OUTPUT_ROOT = Path("packets/math-pilot")
PAGE_IMAGE_DIR = Path("derived/verification")
_PACKET_COUNT = 3
_LAYER_COUNT = 6
_UNITS_PER_LAYER = 10
_UNITS_PER_PACKET = 20
_SAFE_PACKET_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _inside(root: Path, path: Path, label: str) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} must remain inside the project: {path}") from exc
    return resolved


def _project_path(root: Path, value: str | Path, label: str) -> Path:
    path = Path(value)
    return _inside(root, path if path.is_absolute() else root / path, label)


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object: {path}")
    return value


def _asset_binding(
    root: Path,
    value: str,
    expected_sha256: str,
    label: str,
) -> dict[str, str]:
    path = _project_path(root, value, f"{label} path")
    if not path.is_file():
        raise ValueError(f"Missing {label}: {path}")
    actual_sha256 = sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"Stale {label} hash for {path}: expected {expected_sha256}, got {actual_sha256}"
        )
    return {"project_path": _relative(root, path), "sha256": actual_sha256}


def _layer_records(manifest: Mapping[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    basis = manifest.get("selection_basis")
    selected = manifest.get("selected_units")
    if not isinstance(basis, dict) or not isinstance(selected, dict):
        raise ValueError("Pilot manifest must define selection_basis and selected_units objects")
    strata = basis.get("strata")
    if not isinstance(strata, list) or len(strata) != _LAYER_COUNT:
        raise ValueError(f"Pilot manifest must define exactly {_LAYER_COUNT} strata")

    ordered_names: list[str] = []
    for expected_index, value in enumerate(strata, 1):
        if not isinstance(value, dict):
            raise ValueError("Every pilot stratum must be an object")
        if value.get("layer_index") != expected_index or not isinstance(value.get("name"), str):
            raise ValueError("Pilot strata must have consecutive layer_index values and names")
        ordered_names.append(value["name"])
    if set(selected) != set(ordered_names):
        raise ValueError("selected_units keys must exactly match the six declared strata")

    layers: list[tuple[str, list[dict[str, Any]]]] = []
    all_ids: list[str] = []
    for name in ordered_names:
        raw_records = selected[name]
        if not isinstance(raw_records, list) or len(raw_records) != _UNITS_PER_LAYER:
            raise ValueError(
                f"Pilot stratum {name!r} must contain exactly {_UNITS_PER_LAYER} records"
            )
        records: list[dict[str, Any]] = []
        for raw in raw_records:
            if not isinstance(raw, dict) or not isinstance(raw.get("unit_id"), str):
                raise ValueError(f"Pilot stratum {name!r} contains an invalid unit record")
            records.append(dict(raw))
            all_ids.append(raw["unit_id"])
        layers.append((name, records))
    if len(all_ids) != 60 or len(set(all_ids)) != 60:
        duplicates = sorted(unit_id for unit_id, count in Counter(all_ids).items() if count > 1)
        raise ValueError(
            f"Pilot selection must contain exactly 60 unique IDs; duplicates={duplicates}"
        )
    return layers


def _verify_pre_run_hashes(root: Path, manifest: Mapping[str, Any]) -> None:
    values = manifest.get("pre_run_hashes")
    if not isinstance(values, dict):
        raise ValueError("Pilot manifest must contain pre_run_hashes")
    for value, expected in values.items():
        if not isinstance(value, str) or not isinstance(expected, str):
            raise ValueError("pre_run_hashes must map project-relative paths to SHA-256 strings")
        path = _project_path(root, value, "pre-run evidence path")
        if not path.is_file():
            raise ValueError(f"Missing pre-run evidence: {path}")
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(
                f"Stale pre-run evidence hash for {value}: expected {expected}, got {actual}"
            )


def _page_bindings(
    root: Path,
    source: Path,
    pages: set[int],
    output_root: Path,
) -> tuple[dict[int, dict[str, str]], dict[Path, bytes]]:
    bindings: dict[int, dict[str, str]] = {}
    rendered: dict[Path, bytes] = {}
    missing: list[int] = []
    for page in sorted(pages):
        existing = _project_path(
            root,
            PAGE_IMAGE_DIR / f"page-{page:04d}.png",
            f"full-page image path for page {page}",
        )
        if existing.is_file():
            bindings[page] = {
                "project_path": _relative(root, existing),
                "sha256": sha256_file(existing),
            }
        else:
            missing.append(page)

    if missing:
        with fitz.open(source) as document:
            for page in missing:
                if page > document.page_count:
                    raise ValueError(f"Selected unit page {page} exceeds the source PDF page count")
                pixmap = document.load_page(page - 1).get_pixmap(
                    matrix=fitz.Matrix(2.0, 2.0), alpha=False
                )
                content = pixmap.tobytes("png")
                path = _inside(
                    root,
                    output_root / "_rendered-pages" / f"page-{page:04d}.png",
                    f"rendered full-page image path for page {page}",
                )
                rendered[path] = content
                bindings[page] = {
                    "project_path": _relative(root, path),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
    return bindings, rendered


def _candidate_entry(
    root: Path,
    candidate: MathCandidate,
    unit: SourceUnit,
    source_pdf_sha256: str,
) -> dict[str, Any]:
    stale: list[str] = []
    if candidate.source_pdf_sha256 != source_pdf_sha256:
        stale.append("source PDF hash")
    if candidate.source_hash != unit.source_hash:
        stale.append("source hash")
    if candidate.page != unit.page:
        stale.append("page")
    if stale:
        raise ValueError(
            f"Stale math candidate {candidate.candidate_id} for {unit.unit_id}: " + ", ".join(stale)
        )
    record = candidate.model_dump(mode="json")
    return {
        "candidate": record,
        "candidate_record_sha256": _canonical_sha256(record),
        "crop": _asset_binding(
            root,
            candidate.crop_path,
            candidate.crop_sha256,
            f"crop for candidate {candidate.candidate_id}",
        ),
        "raw_response": _asset_binding(
            root,
            candidate.raw_response_path,
            candidate.response_sha256,
            f"raw response for candidate {candidate.candidate_id}",
        ),
    }


def _prompt_family(prompt_version: str) -> str:
    return prompt_version.split(":", 1)[0]


def _select_prompt_contracts(
    candidates_by_unit: Mapping[str, list[MathCandidate]],
    selected_ids: list[str],
    requested_family: str | None,
    requested_versions: Mapping[int, str] | None,
) -> tuple[str, dict[int, str], dict[str, list[MathCandidate]]]:
    selected_set = set(selected_ids)
    if requested_family is not None and not requested_family.strip():
        raise ValueError("prompt_family must not be blank")

    versions_by_family: dict[str, dict[int, dict[str, list[MathCandidate]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for candidates in candidates_by_unit.values():
        for candidate in candidates:
            versions_by_family[_prompt_family(candidate.prompt_version)][candidate.pass_index][
                candidate.prompt_version
            ].append(candidate)

    def complete_contracts(family: str, pass_index: int) -> list[str]:
        complete: list[str] = []
        for version, values in versions_by_family.get(family, {}).get(pass_index, {}).items():
            counts = Counter(value.unit_id for value in values)
            if set(counts) == selected_set and set(counts.values()) == {1}:
                complete.append(version)
        return complete

    chosen_versions: dict[int, str]
    if requested_versions is not None:
        if set(requested_versions) != {1, 2} or not all(
            isinstance(value, str) and value for value in requested_versions.values()
        ):
            raise ValueError(
                "prompt_versions must specify non-empty exact versions for pass 1 and 2"
            )
        chosen_versions = {pass_index: requested_versions[pass_index] for pass_index in (1, 2)}
        exact_families = {_prompt_family(value) for value in chosen_versions.values()}
        if len(exact_families) != 1:
            raise ValueError("Pass 1 and pass 2 prompt versions must belong to the same family")
        chosen_family = exact_families.pop()
        if requested_family is not None and requested_family != chosen_family:
            raise ValueError("prompt_family conflicts with the exact prompt_versions")
        for pass_index, version in chosen_versions.items():
            if version not in complete_contracts(chosen_family, pass_index):
                raise ValueError(
                    f"Prompt contract {version!r} is not complete and unique for pass {pass_index}"
                )
    else:
        candidate_families = (
            [requested_family] if requested_family is not None else sorted(versions_by_family)
        )
        choices: list[tuple[str, dict[int, str], tuple[str, str]]] = []
        for family in candidate_families:
            if family is None:
                continue
            contracts = {
                pass_index: complete_contracts(family, pass_index) for pass_index in (1, 2)
            }
            if not all(contracts.values()):
                continue
            versions: dict[int, str] = {}
            latest_times: list[str] = []
            for pass_index in (1, 2):
                version = max(
                    contracts[pass_index],
                    key=lambda value: (
                        max(
                            candidate.generated_at
                            for candidate in versions_by_family[family][pass_index][value]
                        ),
                        value,
                    ),
                )
                versions[pass_index] = version
                latest_times.append(
                    max(
                        candidate.generated_at
                        for candidate in versions_by_family[family][pass_index][version]
                    )
                )
            choices.append((family, versions, (min(latest_times), max(latest_times))))
        if not choices:
            target = requested_family if requested_family is not None else "any family"
            raise ValueError(f"No complete pass 1/pass 2 prompt contracts exist for {target}")
        chosen_family, chosen_versions, _ = max(choices, key=lambda value: (value[2], value[0]))

    selected: dict[str, list[MathCandidate]] = {}
    for unit_id in selected_ids:
        values = [
            candidate
            for candidate in candidates_by_unit[unit_id]
            if candidate.prompt_version == chosen_versions[candidate.pass_index]
        ]
        values.sort(key=lambda candidate: (candidate.pass_index, candidate.candidate_id))
        if len(values) != 2 or [value.pass_index for value in values] != [1, 2]:
            raise ValueError(
                f"Selected unit {unit_id} does not have a unique pass 1/pass 2 pair "
                f"for prompt family {chosen_family}"
            )
        if {_prompt_family(value.prompt_version) for value in values} != {chosen_family}:
            raise AssertionError("Internal prompt selection mixed prompt families")
        selected[unit_id] = values
    return chosen_family, chosen_versions, selected


def _write_packet(packet_dir: Path, manifest: Mapping[str, Any]) -> None:
    readme = (
        "# Pilot review result\n\n"
        "Record reviewer-authored results in this directory. Bind each result to "
        "`../packet/manifest.json` and its `payload_sha256`. The packet itself contains "
        "source evidence and candidate transcriptions only; it contains no review decision.\n"
    )
    manifest_path = packet_dir / "packet" / "manifest.json"
    readme_path = packet_dir / "result" / "README.md"
    if packet_dir.exists():
        if not manifest_path.is_file() or not readme_path.is_file():
            raise ValueError(f"Existing immutable pilot packet is incomplete: {packet_dir}")
        existing = _json_object(manifest_path, "existing pilot packet manifest")
        if existing != manifest or readme_path.read_text(encoding="utf-8") != readme:
            raise ValueError(
                f"Existing immutable pilot packet conflicts with planned content: {packet_dir}"
            )
        return
    atomic_write_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    atomic_write_text(readme_path, readme)


def build_math_pilot_packets(
    root: Path,
    output_root: Path | None = None,
    packet_prefix: str = "pilot-review",
    prompt_family: str | None = None,
    prompt_versions: Mapping[int, str] | None = None,
) -> dict[str, Any]:
    """Generate three deterministic, exact-ID packets from the 60-unit pilot manifest."""

    project_root = Path(root).resolve()
    if not project_root.is_dir():
        raise ValueError(f"Project root does not exist: {project_root}")
    if not _SAFE_PACKET_PREFIX_RE.fullmatch(packet_prefix):
        raise ValueError("packet_prefix must be a safe single path component")
    destination = _inside(
        project_root,
        project_root / DEFAULT_OUTPUT_ROOT
        if output_root is None
        else _project_path(project_root, output_root, "pilot packet output root"),
        "pilot packet output root",
    )

    selection_path = project_root / PILOT_MANIFEST_PATH
    manifest = _json_object(selection_path, "pilot selection manifest")
    layers = _layer_records(manifest)
    _verify_pre_run_hashes(project_root, manifest)

    config = load_project(project_root)
    source = _project_path(project_root, config.source_path, "source PDF path")
    if not source.is_file():
        raise ValueError(f"Source PDF is missing: {source}")
    source_pdf_sha256 = sha256_file(source)
    if source_pdf_sha256 != config.source_sha256:
        raise ValueError("Source PDF hash changed after project initialization")
    if manifest.get("source_pdf_sha256") != source_pdf_sha256:
        raise ValueError("Pilot manifest is bound to a different source PDF hash")
    if manifest.get("project_id") != config.project_id:
        raise ValueError("Pilot manifest is bound to a different project ID")

    selected_ids = [record["unit_id"] for _, records in layers for record in records]
    selected_set = set(selected_ids)
    units_by_id: dict[str, SourceUnit] = {}
    for unit in read_jsonl(project_root / UNITS_PATH, SourceUnit):
        if unit.unit_id not in selected_set:
            continue
        if unit.unit_id in units_by_id:
            raise ValueError(f"Duplicate selected SourceUnit ID in ledger: {unit.unit_id}")
        if unit.source_hash != sha256_text(unit.source_text):
            raise ValueError(f"Stale SourceUnit source_hash: {unit.unit_id}")
        units_by_id[unit.unit_id] = unit
    missing_units = sorted(selected_set - units_by_id.keys())
    if missing_units:
        raise ValueError(f"Selected SourceUnit IDs are missing from the ledger: {missing_units}")
    for layer_name, records in layers:
        for record in records:
            unit = units_by_id[record["unit_id"]]
            if record.get("page") != unit.page or record.get("kind") != unit.kind.value:
                raise ValueError(
                    f"Stale pilot selection metadata for {unit.unit_id} in {layer_name}"
                )

    candidate_pool_by_unit: dict[str, list[MathCandidate]] = defaultdict(list)
    candidate_ids: set[str] = set()
    for candidate in read_jsonl(project_root / CANDIDATES_PATH, MathCandidate):
        if candidate.unit_id not in selected_set:
            continue
        if candidate.candidate_id in candidate_ids:
            raise ValueError(f"Duplicate selected math candidate ID: {candidate.candidate_id}")
        candidate_ids.add(candidate.candidate_id)
        candidate_pool_by_unit[candidate.unit_id].append(candidate)
    prompt_family, selected_prompt_versions, candidates_by_unit = _select_prompt_contracts(
        candidate_pool_by_unit,
        selected_ids,
        prompt_family,
        prompt_versions,
    )

    page_bindings, rendered_pages = _page_bindings(
        project_root,
        source,
        {units_by_id[unit_id].page for unit_id in selected_ids},
        destination,
    )
    assignments: list[list[tuple[str, dict[str, Any]]]] = [[], [], []]
    for layer_position, (layer_name, records) in enumerate(layers):
        for unit_position, record in enumerate(records):
            assignments[(unit_position + layer_position) % _PACKET_COUNT].append(
                (layer_name, record)
            )
    if [len(values) for values in assignments] != [_UNITS_PER_PACKET] * _PACKET_COUNT:
        raise AssertionError("Internal pilot allocation did not produce three 20-unit packets")

    planned: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    for packet_index, assignment in enumerate(assignments, 1):
        packet_id = f"{packet_prefix}-{packet_index:02d}"
        layer_counts = Counter(layer for layer, _ in assignment)
        if set(layer_counts.values()) - {3, 4}:
            raise AssertionError("Internal pilot allocation is not balanced by layer")
        unit_entries: list[dict[str, Any]] = []
        for layer, selection_record in assignment:
            unit = units_by_id[selection_record["unit_id"]]
            unit_record = unit.model_dump(mode="json")
            unit_entries.append(
                {
                    "layer": layer,
                    "selection_record": selection_record,
                    "source_unit": unit_record,
                    "source_hash": unit.source_hash,
                    "source_unit_record_sha256": _canonical_sha256(unit_record),
                    "full_page_image": page_bindings[unit.page],
                    "candidates": [
                        _candidate_entry(project_root, value, unit, source_pdf_sha256)
                        for value in candidates_by_unit[unit.unit_id]
                    ],
                }
            )
        payload: dict[str, Any] = {
            "schema_version": 1,
            "kind": "qasc-exact-id-math-pilot-review-packet",
            "packet_id": packet_id,
            "source_pdf": {
                "project_path": _relative(project_root, source),
                "sha256": source_pdf_sha256,
            },
            "selection_manifest": {
                "project_path": PILOT_MANIFEST_PATH.as_posix(),
                "sha256": sha256_file(selection_path),
            },
            "prompt_contract": {
                "family": prompt_family,
                "pass_versions": {
                    str(pass_index): selected_prompt_versions[pass_index] for pass_index in (1, 2)
                },
            },
            "unit_count": len(unit_entries),
            "layer_counts": {name: layer_counts[name] for name, _ in layers},
            "unit_sequence": [entry["source_unit"]["unit_id"] for entry in unit_entries],
            "units": unit_entries,
        }
        packet_manifest = {"payload": payload, "payload_sha256": _canonical_sha256(payload)}
        packet_dir = destination / packet_id
        summary = {
            "packet_id": packet_id,
            "unit_ids": payload["unit_sequence"],
            "layer_counts": payload["layer_counts"],
            "payload_sha256": packet_manifest["payload_sha256"],
            "manifest_path": _relative(project_root, packet_dir / "packet" / "manifest.json"),
        }
        planned.append((packet_dir, packet_manifest, summary))

    assigned_ids = [unit_id for _, _, summary in planned for unit_id in summary["unit_ids"]]
    if len(assigned_ids) != 60 or set(assigned_ids) != selected_set:
        raise AssertionError("Internal pilot allocation lost or duplicated selected IDs")

    for path, content in rendered_pages.items():
        atomic_write_bytes(path, content)
    for packet_dir, packet_manifest, _ in planned:
        _write_packet(packet_dir, packet_manifest)
    return {
        "output_root": _relative(project_root, destination),
        "source_pdf_sha256": source_pdf_sha256,
        "prompt_family": prompt_family,
        "prompt_versions": selected_prompt_versions,
        "unit_count": len(assigned_ids),
        "packets": [summary for _, _, summary in planned],
    }
