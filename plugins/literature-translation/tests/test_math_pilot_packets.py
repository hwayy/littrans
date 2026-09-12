from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pymupdf as fitz
import pytest

from littrans.math_pilot_packets import build_math_pilot_packets
from littrans.models import (
    MathCandidate,
    MathCandidateClassification,
    ProjectConfig,
    SourceUnit,
    UnitKind,
)
from littrans.storage import sha256_file, sha256_text, write_jsonl, write_yaml


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    for relative in ("source", "derived/verification", "evidence/math", "overrides"):
        (root / relative).mkdir(parents=True)
    source = root / "source/source.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((30, 30), "pilot source page")
    document.save(source)
    document.close()
    write_yaml(
        root / "project.yaml",
        ProjectConfig(
            project_id="pilot-test",
            title="Pilot test",
            source_path="source/source.pdf",
            source_sha256=sha256_file(source),
            source_pages=1,
            profile="research-paper",
        ).model_dump(mode="json", exclude_none=True),
    )
    (root / "overrides/layout.yaml").write_text("overrides: []\n", encoding="utf-8")
    (root / "derived/verification.json").write_text("{}\n", encoding="utf-8")

    units: list[SourceUnit] = []
    candidates: list[MathCandidate] = []
    crop = root / "evidence/math/crop.png"
    crop.write_bytes(b"shared crop")
    raw = root / "evidence/math/raw.json"
    raw.write_text('{"candidate":"x"}', encoding="utf-8")
    layers: dict[str, list[dict[str, Any]]] = {}
    strata: list[dict[str, Any]] = []
    for layer_index in range(6):
        layer = f"layer-{layer_index + 1}"
        strata.append({"layer_index": layer_index + 1, "name": layer})
        layers[layer] = []
        for unit_index in range(10):
            unit_id = f"p0001-u{layer_index * 10 + unit_index + 1:03d}-pilot"
            text = f"x_{layer_index}_{unit_index}=1"
            unit = SourceUnit(
                unit_id=unit_id,
                kind=UnitKind.EQUATION,
                page=1,
                bbox=(20, float(unit_index), 200, float(unit_index + 1)),
                source_text=text,
                source_hash=sha256_text(text),
                latex=text,
                confidence=0.8,
            )
            units.append(unit)
            layers[layer].append({"unit_id": unit_id, "page": 1, "kind": "equation"})
            for family_index, family in enumerate(("math-vision-v1", "math-vision-v2"), 1):
                for pass_index in (1, 2):
                    candidates.append(
                        MathCandidate(
                            candidate_id=(
                                f"candidate-{layer_index}-{unit_index}-{family_index}-{pass_index}"
                            ),
                            unit_id=unit_id,
                            page=1,
                            source_pdf_sha256=sha256_file(source),
                            source_hash=unit.source_hash,
                            crop_path="evidence/math/crop.png",
                            crop_sha256=sha256_file(crop),
                            provider="test",
                            model="test",
                            prompt_version=f"{family}:contract-{pass_index}",
                            pass_index=pass_index,
                            classification=MathCandidateClassification.DISPLAY,
                            latex=text,
                            request_sha256=sha256_text(f"request-{family}-{pass_index}"),
                            response_sha256=sha256_file(raw),
                            raw_response_path="evidence/math/raw.json",
                            generated_at=f"2026-08-2{family_index}T00:00:0{pass_index}+00:00",
                        )
                    )
    write_jsonl(root / "derived/units.jsonl", units)
    write_jsonl(root / "evidence/math/candidates.jsonl", candidates)
    pre_run = {
        value: sha256_file(root / value)
        for value in (
            "derived/units.jsonl",
            "overrides/layout.yaml",
            "derived/verification.json",
        )
    }
    pilot = {
        "schema_version": 1,
        "pilot_id": "pilot-test-60",
        "project_id": "pilot-test",
        "source_pdf_sha256": sha256_file(source),
        "selection_basis": {"strata": strata},
        "pre_run_hashes": pre_run,
        "selected_units": layers,
    }
    (root / "evidence/math/pilot-60-manifest.json").write_text(
        json.dumps(pilot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return root


def test_builds_three_balanced_exact_id_packets_with_complete_bindings(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    result = build_math_pilot_packets(root)
    assert result["unit_count"] == 60
    assert result["prompt_family"] == "math-vision-v2"
    assert result["prompt_versions"] == {
        1: "math-vision-v2:contract-1",
        2: "math-vision-v2:contract-2",
    }
    assert [len(packet["unit_ids"]) for packet in result["packets"]] == [20, 20, 20]
    assert len({unit_id for packet in result["packets"] for unit_id in packet["unit_ids"]}) == 60
    assert all(set(packet["layer_counts"].values()) == {3, 4} for packet in result["packets"])

    manifest_path = root / result["packets"][0]["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["payload_sha256"] == _canonical_sha256(manifest["payload"])
    unit = manifest["payload"]["units"][0]
    assert unit["source_hash"] == unit["source_unit"]["source_hash"]
    assert [value["candidate"]["pass_index"] for value in unit["candidates"]] == [1, 2]
    assert {
        value["candidate"]["prompt_version"].split(":", 1)[0] for value in unit["candidates"]
    } == {"math-vision-v2"}
    assert all(value["crop"]["sha256"] for value in unit["candidates"])
    assert all(value["raw_response"]["sha256"] for value in unit["candidates"])
    page = root / unit["full_page_image"]["project_path"]
    assert page.is_file()
    assert unit["full_page_image"]["sha256"] == sha256_file(page)
    assert (manifest_path.parent.parent / "result/README.md").is_file()


def test_rejects_stale_candidate_before_writing_packets(tmp_path: Path) -> None:
    root = _project(tmp_path)
    path = root / "evidence/math/candidates.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["source_hash"] = "0" * 64
    lines[0] = json.dumps(first)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Stale math candidate"):
        build_math_pilot_packets(root, prompt_family="math-vision-v1")
    assert not (root / "packets/math-pilot").exists()


def test_prompt_family_and_prefix_are_selectable_and_existing_packets_are_immutable(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    first = build_math_pilot_packets(root, prompt_family="math-vision-v1")
    first_manifest = root / first["packets"][0]["manifest_path"]
    before = first_manifest.read_bytes()

    retest = build_math_pilot_packets(root, packet_prefix="pilot-retest-review")
    assert retest["prompt_family"] == "math-vision-v2"
    assert all(
        packet["packet_id"].startswith("pilot-retest-review-") for packet in retest["packets"]
    )
    assert first_manifest.read_bytes() == before
    with pytest.raises(ValueError, match="immutable pilot packet conflicts"):
        build_math_pilot_packets(root)
    assert first_manifest.read_bytes() == before


def test_exact_prompt_versions_must_form_one_complete_family(tmp_path: Path) -> None:
    root = _project(tmp_path)
    with pytest.raises(ValueError, match="same family"):
        build_math_pilot_packets(
            root,
            prompt_versions={
                1: "math-vision-v1:contract-1",
                2: "math-vision-v2:contract-2",
            },
        )
