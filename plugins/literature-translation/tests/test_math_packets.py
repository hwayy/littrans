from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pymupdf as fitz
import pytest

from littrans.math_packets import build_math_review_packets
from littrans.models import (
    MathCandidate,
    MathCandidateClassification,
    ProjectConfig,
    SemanticStatus,
    SourceUnit,
    UnitKind,
)
from littrans.storage import sha256_file, sha256_text, write_json, write_jsonl, write_yaml


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _project(tmp_path: Path, page_counts: list[int]) -> tuple[Path, list[SourceUnit]]:
    root = tmp_path / "project"
    (root / "source").mkdir(parents=True)
    (root / "derived" / "verification").mkdir(parents=True)
    (root / "evidence" / "math").mkdir(parents=True)
    (root / "overrides").mkdir()
    source = root / "source" / "source.pdf"
    document = fitz.open()
    units: list[SourceUnit] = []
    for page_number, count in enumerate(page_counts, 1):
        page = document.new_page()
        page.insert_text((30, 30), f"page {page_number}")
        (root / "derived" / "verification" / f"page-{page_number:04d}.png").write_bytes(
            f"page-image-{page_number}".encode()
        )
        for index in range(count):
            text = f"x_{{{page_number},{index}}}=1"
            units.append(
                SourceUnit(
                    unit_id=f"p{page_number:04d}-u{index + 1:03d}-equation",
                    kind=UnitKind.EQUATION,
                    page=page_number,
                    bbox=(20, float(index), 200, float(index + 1)),
                    source_text=text,
                    source_hash=sha256_text(text),
                    latex=text,
                    math_status=SemanticStatus.UNVERIFIED,
                    confidence=0.6,
                )
            )
    document.save(source)
    document.close()
    write_yaml(
        root / "project.yaml",
        ProjectConfig(
            project_id="packet-test",
            title="Packet test",
            source_path="source/source.pdf",
            source_sha256=sha256_file(source),
            source_pages=len(page_counts),
            profile="research-paper",
        ).model_dump(mode="json", exclude_none=True),
    )
    write_jsonl(root / "derived" / "units.jsonl", units)
    write_json(root / "derived" / "verification.json", {"errors": []})
    return root, units


def _add_candidate(root: Path, unit: SourceUnit) -> MathCandidate:
    crop = root / "evidence" / "math" / "crop.png"
    raw = root / "evidence" / "math" / "response.json"
    crop.write_bytes(b"crop evidence")
    raw.write_text('{"visible":"x=1"}', encoding="utf-8")
    candidate = MathCandidate(
        candidate_id="candidate-one",
        unit_id=unit.unit_id,
        page=unit.page,
        source_pdf_sha256=sha256_file(root / "source" / "source.pdf"),
        source_hash=unit.source_hash,
        crop_path="evidence/math/crop.png",
        crop_sha256=sha256_file(crop),
        provider="test-provider",
        model="test-model",
        prompt_version="test-v1",
        classification=MathCandidateClassification.DISPLAY,
        latex="x=1",
        request_sha256=sha256_text("request"),
        response_sha256=sha256_file(raw),
        raw_response_path="evidence/math/response.json",
    )
    write_jsonl(root / "evidence" / "math" / "candidates.jsonl", [candidate])
    return candidate


def test_packets_use_complete_consecutive_pages_and_respect_hard_limit(tmp_path: Path) -> None:
    root, _ = _project(tmp_path, [20, 20, 20, 0, 10])
    result = build_math_review_packets(root, target_units=40, max_units=60)
    assert [packet["pages"] for packet in result["packets"]] == [[1, 2], [3, 4, 5]]
    assert [packet["math_unit_count"] for packet in result["packets"]] == [40, 30]
    assert all(packet["math_unit_count"] <= 60 for packet in result["packets"])
    for summary in result["packets"]:
        manifest = json.loads((root / summary["manifest_path"]).read_text(encoding="utf-8"))
        assert manifest["pages"][-1]["page"] - manifest["pages"][0]["page"] + 1 == len(
            manifest["pages"]
        )


def test_single_high_density_page_is_the_only_limit_exception(tmp_path: Path) -> None:
    root, _ = _project(tmp_path, [5, 61, 5])
    result = build_math_review_packets(root, target_units=40, max_units=60)
    assert [packet["pages"] for packet in result["packets"]] == [[1], [2], [3]]
    assert [packet["density_exception"] for packet in result["packets"]] == [False, True, False]
    assert result["packets"][1]["math_unit_count"] == 61


def test_manifest_hashes_are_stable_and_bind_complete_evidence(tmp_path: Path) -> None:
    root, units = _project(tmp_path, [1])
    candidate = _add_candidate(root, units[0])
    write_json(
        root / "derived" / "verification.json",
        {
            "errors": [
                {
                    "code": "overlapping-units",
                    "page": 1,
                    "unit_id": units[0].unit_id,
                    "other_unit_id": "nearby",
                }
            ]
        },
    )
    first = build_math_review_packets(root)
    second = build_math_review_packets(root)
    assert first == second
    summary = first["packets"][0]
    manifest_path = root / summary["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    manifest_hash = manifest.pop("manifest_sha256")
    assert _canonical_sha256(manifest) == manifest_hash
    packet_hash = manifest.pop("packet_sha256")
    manifest.pop("packet_id")
    assert _canonical_sha256(manifest) == packet_hash
    entry = manifest["units"][0]
    assert entry["unit"] == units[0].model_dump(mode="json")
    assert entry["source_hash"] == units[0].source_hash
    assert entry["candidates"][0]["candidate"] == candidate.model_dump(mode="json")
    assert entry["candidates"][0]["candidate_record_sha256"] == _canonical_sha256(
        candidate.model_dump(mode="json")
    )
    assert manifest["verification"]["overlap_relations"]
    assert (manifest_path.parent / manifest["pages"][0]["image"]["packet_path"]).is_file()
    assert (root / summary["result_path"] / "README.md").is_file()
    assert not list((root / summary["result_path"]).glob("*.json*"))


def test_stale_candidate_is_rejected_before_output_is_written(tmp_path: Path) -> None:
    root, units = _project(tmp_path, [1])
    candidate = _add_candidate(root, units[0])
    stale = candidate.model_copy(update={"source_hash": "0" * 64})
    write_jsonl(root / "evidence" / "math" / "candidates.jsonl", [stale])
    with pytest.raises(ValueError, match="Stale math candidate"):
        build_math_review_packets(root)
    assert not (root / ".littrans" / "work" / "math-review-packets").exists()


def test_builder_does_not_modify_authoritative_or_shared_review_files(tmp_path: Path) -> None:
    root, _ = _project(tmp_path, [2])
    layout = root / "overrides" / "layout.yaml"
    layout.write_text("overrides: []\n", encoding="utf-8")
    reviews = root / "evidence" / "math" / "reviews.jsonl"
    reviews.write_text('{"other_reviewer":"private"}\n', encoding="utf-8")
    protected = [
        root / "derived" / "units.jsonl",
        root / "derived" / "verification.json",
        layout,
        reviews,
    ]
    before = {path: path.read_bytes() for path in protected}
    result = build_math_review_packets(root, manual_only=True)
    assert {path: path.read_bytes() for path in protected} == before
    manifest_text = (root / result["packets"][0]["manifest_path"]).read_text(encoding="utf-8")
    assert "other_reviewer" not in manifest_text


def test_asset_and_output_path_traversal_are_rejected(tmp_path: Path) -> None:
    root, units = _project(tmp_path, [1])
    candidate = _add_candidate(root, units[0]).model_copy(update={"crop_path": "../../outside.png"})
    write_jsonl(root / "evidence" / "math" / "candidates.jsonl", [candidate])
    with pytest.raises(ValueError, match="inside the project"):
        build_math_review_packets(root)
    with pytest.raises(ValueError, match="inside the project"):
        build_math_review_packets(root, output_root=tmp_path / "outside")


def test_manual_only_builds_without_candidates_and_binds_local_visual_evidence(
    tmp_path: Path,
) -> None:
    root, units = _project(tmp_path, [1])
    crop = root / "derived" / "assets" / "unit-crop.png"
    crop.parent.mkdir(parents=True)
    crop.write_bytes(b"local unit crop")
    unit = SourceUnit.model_validate(
        {
            **units[0].model_dump(mode="json"),
            "asset_refs": [
                {
                    "kind": "equation",
                    "path": "derived/assets/unit-crop.png",
                    "bbox": (20, 0, 200, 1),
                }
            ],
        }
    )
    write_jsonl(root / "derived" / "units.jsonl", [unit])
    write_json(
        root / "derived" / "verification.json",
        {
            "errors": [
                {
                    "code": "overlapping-units",
                    "page": 1,
                    "unit_id": unit.unit_id,
                    "other_unit_id": "adjacent-unit",
                }
            ]
        },
    )

    first = build_math_review_packets(root, manual_only=True, require_candidates=True)
    second = build_math_review_packets(root, manual_only=True, require_candidates=True)
    assert first == second
    manifest_path = root / first["packets"][0]["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["review_mode"] == "manual-only"
    assert manifest["source_pdf"] == {
        "configured_path": "source/source.pdf",
        "project_path": "source/source.pdf",
        "sha256": sha256_file(root / "source" / "source.pdf"),
    }
    assert manifest["units"][0]["unit"] == unit.model_dump(mode="json")
    assert manifest["units"][0]["candidates"] == []
    assert manifest["units"][0]["assets"][0]["sha256"] == sha256_file(crop)
    review_crop = manifest["units"][0]["review_crop"]
    assert review_crop["source_pdf_page"] == unit.page
    assert review_crop["unit_bbox"] == list(unit.bbox)
    assert review_crop["render"] == {
        "engine": "PyMuPDF",
        "matrix": [3.0, 3.0],
        "padding_points": 4.0,
        "alpha": False,
    }
    review_crop_path = manifest_path.parent / review_crop["packet_path"]
    assert review_crop_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert sha256_file(review_crop_path) == review_crop["sha256"]
    assert manifest["verification"]["overlap_relations"] == [
        {
            "verification_field": "errors",
            "relation": {
                "code": "overlapping-units",
                "page": 1,
                "unit_id": unit.unit_id,
                "other_unit_id": "adjacent-unit",
            },
        }
    ]
    page_image = manifest_path.parent / manifest["pages"][0]["image"]["packet_path"]
    assert page_image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert sha256_file(page_image) == manifest["pages"][0]["image"]["sha256"]
    result_dir = root / first["packets"][0]["result_path"]
    assert result_dir.is_dir()
    assert list(result_dir.iterdir()) == []

    manifest_hash = manifest.pop("manifest_sha256")
    assert _canonical_sha256(manifest) == manifest_hash
    packet_hash = manifest.pop("packet_sha256")
    manifest.pop("packet_id")
    assert _canonical_sha256(manifest) == packet_hash


def test_manual_only_snapshots_review_crop_when_inline_unit_has_no_assets(
    tmp_path: Path,
) -> None:
    root, units = _project(tmp_path, [1])
    inline = units[0].model_copy(
        update={
            "kind": UnitKind.PARAGRAPH,
            "source_text": "The value $x$ is known.",
            "source_markdown": "The value $x$ is known.",
            "source_hash": sha256_text("The value $x$ is known."),
            "asset_refs": [],
        }
    )
    write_jsonl(root / "derived" / "units.jsonl", [inline])

    result = build_math_review_packets(root, manual_only=True)
    manifest_path = root / result["packets"][0]["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["units"][0]
    assert entry["assets"] == []
    assert entry["review_crop"]["packet_path"].startswith("assets/review-crops/")
    crop = manifest_path.parent / entry["review_crop"]["packet_path"]
    assert crop.is_file()
    assert sha256_file(crop) == entry["review_crop"]["sha256"]


def test_manual_only_never_reads_or_includes_remote_candidates(tmp_path: Path) -> None:
    root, units = _project(tmp_path, [1])
    candidate = _add_candidate(root, units[0])
    (root / candidate.raw_response_path).unlink()
    candidates_path = root / "evidence" / "math" / "candidates.jsonl"
    candidates_path.write_text(
        candidate.model_dump_json() + "\nthis is deliberately not valid jsonl\n",
        encoding="utf-8",
    )

    result = build_math_review_packets(root, manual_only=True)
    manifest_text = (root / result["packets"][0]["manifest_path"]).read_text(encoding="utf-8")
    assert candidate.candidate_id not in manifest_text
    assert candidate.provider not in manifest_text
    assert "raw_response" not in manifest_text


def test_manual_only_selects_only_current_unverified_inline_or_display_math(
    tmp_path: Path,
) -> None:
    root, units = _project(tmp_path, [3])
    verified_display = units[0].model_copy(update={"math_status": SemanticStatus.VERIFIED})
    unverified_inline = SourceUnit.model_validate(
        {
            **units[1].model_dump(mode="json"),
            "kind": UnitKind.PARAGRAPH,
            "source_markdown": "Let $x=1$.",
            "math_status": SemanticStatus.UNVERIFIED,
        }
    )
    non_math = SourceUnit.model_validate(
        {
            **units[2].model_dump(mode="json"),
            "kind": UnitKind.PARAGRAPH,
            "source_markdown": "Plain prose.",
            "math_status": None,
        }
    )
    write_jsonl(
        root / "derived" / "units.jsonl",
        [verified_display, unverified_inline, non_math],
    )

    result = build_math_review_packets(root, manual_only=True)
    assert result["math_unit_count"] == 1
    manifest = json.loads(
        (root / result["packets"][0]["manifest_path"]).read_text(encoding="utf-8")
    )
    assert manifest["unit_sequence"] == [unverified_inline.unit_id]


def test_manual_only_can_explicitly_include_verified_non_math_unit_deterministically(
    tmp_path: Path,
) -> None:
    root, units = _project(tmp_path, [3])
    unverified_math = units[0]
    verified_paragraph = units[1].model_copy(
        update={
            "kind": UnitKind.PARAGRAPH,
            "source_text": "This paragraph continues on the same page.",
            "source_markdown": "This paragraph continues on the same page.",
            "source_hash": sha256_text("This paragraph continues on the same page."),
            "latex": None,
            "math_status": SemanticStatus.VERIFIED,
        }
    )
    non_math = units[2].model_copy(
        update={
            "kind": UnitKind.PARAGRAPH,
            "source_text": "Plain prose.",
            "source_markdown": "Plain prose.",
            "source_hash": sha256_text("Plain prose."),
            "latex": None,
            "math_status": None,
        }
    )
    write_jsonl(
        root / "derived" / "units.jsonl",
        [unverified_math, verified_paragraph, non_math],
    )

    default = build_math_review_packets(
        root,
        manual_only=True,
        output_root=Path("packets/default"),
    )
    default_manifest = json.loads(
        (root / default["packets"][0]["manifest_path"]).read_text(encoding="utf-8")
    )
    assert default_manifest["unit_sequence"] == [unverified_math.unit_id]
    assert "explicit_unit_ids" not in default_manifest

    first = build_math_review_packets(
        root,
        manual_only=True,
        output_root=Path("packets/explicit-a"),
        include_unit_ids=[verified_paragraph.unit_id, unverified_math.unit_id],
    )
    second = build_math_review_packets(
        root,
        manual_only=True,
        output_root=Path("packets/explicit-b"),
        include_unit_ids=[unverified_math.unit_id, verified_paragraph.unit_id],
    )
    first_manifest = json.loads(
        (root / first["packets"][0]["manifest_path"]).read_text(encoding="utf-8")
    )
    second_manifest = json.loads(
        (root / second["packets"][0]["manifest_path"]).read_text(encoding="utf-8")
    )
    assert first_manifest == second_manifest
    assert first_manifest["unit_sequence"] == [
        unverified_math.unit_id,
        verified_paragraph.unit_id,
    ]
    assert first_manifest["explicit_unit_ids"] == [
        unverified_math.unit_id,
        verified_paragraph.unit_id,
    ]
    assert first_manifest["math_unit_count"] == 2
    verified_entry = next(
        entry
        for entry in first_manifest["units"]
        if entry["unit"]["unit_id"] == verified_paragraph.unit_id
    )
    assert verified_entry["candidates"] == []
    assert verified_entry["review_crop"]["render"]["matrix"] == [3.0, 3.0]
    page_image = first_manifest["pages"][0]["image"]
    assert page_image["render"]["matrix"] == [2.0, 2.0]


@pytest.mark.parametrize(
    ("include_unit_ids", "pages", "message"),
    [
        (["missing"], "all", "unknown include_unit_ids"),
        (["p0002-u001-equation"], "1", "outside the selected pages"),
        (["p0001-u001-equation", "p0001-u001-equation"], "all", "duplicates"),
        ([""], "all", "empty values"),
    ],
)
def test_explicit_packet_unit_ids_fail_safely(
    tmp_path: Path,
    include_unit_ids: list[str],
    pages: str,
    message: str,
) -> None:
    root, _ = _project(tmp_path, [1, 1])
    with pytest.raises(ValueError, match=message):
        build_math_review_packets(
            root,
            page_spec=pages,
            manual_only=True,
            include_unit_ids=include_unit_ids,
        )
    assert not (root / ".littrans" / "work" / "math-review-packets").exists()


def test_explicit_packet_units_require_manual_only_mode(tmp_path: Path) -> None:
    root, units = _project(tmp_path, [1])
    with pytest.raises(ValueError, match="requires manual_only"):
        build_math_review_packets(root, include_unit_ids=[units[0].unit_id])


def test_manual_only_preserves_consecutive_pages_and_density_exception(
    tmp_path: Path,
) -> None:
    root, _ = _project(tmp_path, [20, 20, 61, 0, 5])
    result = build_math_review_packets(
        root,
        target_units=40,
        max_units=60,
        manual_only=True,
    )
    assert [packet["pages"] for packet in result["packets"]] == [[1, 2], [3], [4, 5]]
    assert [packet["math_unit_count"] for packet in result["packets"]] == [40, 61, 5]
    assert [packet["density_exception"] for packet in result["packets"]] == [
        False,
        True,
        False,
    ]


def test_require_candidates_remains_available_outside_manual_mode(tmp_path: Path) -> None:
    root, units = _project(tmp_path, [2])
    _add_candidate(root, units[0])
    with pytest.raises(ValueError, match="required for every selected unit"):
        build_math_review_packets(root, require_candidates=True)
    assert not (root / ".littrans" / "work" / "math-review-packets").exists()


def test_manual_only_rejects_stale_source_units_without_mutating_shared_files(
    tmp_path: Path,
) -> None:
    root, units = _project(tmp_path, [1])
    units_path = root / "derived" / "units.jsonl"
    verification_path = root / "derived" / "verification.json"
    stale = units[0].model_copy(update={"source_hash": "0" * 64})
    write_jsonl(units_path, [stale])
    before = {
        units_path: units_path.read_bytes(),
        verification_path: verification_path.read_bytes(),
    }
    with pytest.raises(ValueError, match="Stale SourceUnit source_hash"):
        build_math_review_packets(root, manual_only=True)
    assert {path: path.read_bytes() for path in before} == before
    assert not (root / ".littrans" / "work" / "math-review-packets").exists()
