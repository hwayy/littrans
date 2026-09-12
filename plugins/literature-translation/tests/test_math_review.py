from __future__ import annotations

import json
import shutil
from pathlib import Path

import pymupdf as fitz
import pytest
import yaml

from littrans.extractor import apply_layout_overrides
from littrans.math_packets import build_math_review_packets
from littrans.math_review import (
    build_math_review_report,
    import_math_review,
    repair_math_structural_review_ledger,
)
from littrans.models import (
    MathCandidate,
    MathCandidateClassification,
    MathReviewDecision,
    MathReviewDisposition,
    MathStructuralOverrideDecision,
    ProjectConfig,
    SemanticStatus,
    SourceUnit,
    UnitKind,
    canonical_math_structural_override_sha256,
)
from littrans.storage import (
    read_jsonl,
    sha256_file,
    sha256_text,
    write_json,
    write_jsonl,
    write_yaml,
)


def _project(tmp_path: Path) -> tuple[Path, SourceUnit, SourceUnit, MathCandidate, MathCandidate]:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "source.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=220)
    page.insert_text((30, 60), "x = y + 1", fontsize=16)
    page.insert_text((30, 140), "The value $x$ is known.", fontsize=12)
    document.save(source)
    document.close()

    source_hash = sha256_file(source)
    config = ProjectConfig(
        project_id="math-test",
        title="Math test",
        source_path=str(source),
        source_sha256=source_hash,
        source_pages=1,
        profile="en-zh-cn",
    )
    write_yaml(root / "project.yaml", config.model_dump(mode="json", exclude_none=True))
    (root / "derived").mkdir()
    (root / "derived" / "verification").mkdir()
    (root / "derived" / "assets").mkdir(parents=True)
    (root / "evidence" / "math").mkdir(parents=True)
    (root / "overrides").mkdir()

    equation_text = "x = y + 1"
    inline_text = "The value $x$ is known."
    equation_crop = root / "derived" / "assets" / "equation.png"
    inline_crop = root / "derived" / "assets" / "inline.png"
    equation_crop.write_bytes(b"equation crop")
    inline_crop.write_bytes(b"inline crop")
    equation = SourceUnit(
        unit_id="p0001-u001-equation",
        kind=UnitKind.EQUATION,
        page=1,
        bbox=(20, 35, 180, 75),
        source_text=equation_text,
        source_hash=sha256_text(equation_text),
        latex="x = y + 1",
        equation_number="3",
        asset_refs=[
            {"kind": "equation", "path": "derived/assets/equation.png", "bbox": (20, 35, 180, 75)}
        ],
        confidence=0.7,
    )
    inline = SourceUnit(
        unit_id="p0001-u002-inline",
        kind=UnitKind.PARAGRAPH,
        page=1,
        bbox=(20, 110, 230, 155),
        source_text=inline_text,
        source_hash=sha256_text(inline_text),
        source_markdown=inline_text,
        asset_refs=[
            {"kind": "math", "path": "derived/assets/inline.png", "bbox": (20, 110, 230, 155)}
        ],
        confidence=0.7,
    )
    write_jsonl(root / "derived" / "units.jsonl", [equation, inline])

    def candidate(
        candidate_id: str,
        unit: SourceUnit,
        crop: Path,
        classification: MathCandidateClassification,
        latex: str | None,
        markdown: str | None,
        pass_index: int,
    ) -> MathCandidate:
        raw = root / "evidence" / "math" / f"{candidate_id}.json"
        raw.write_text(json.dumps({"candidate": candidate_id}), encoding="utf-8")
        return MathCandidate(
            candidate_id=candidate_id,
            unit_id=unit.unit_id,
            page=1,
            source_pdf_sha256=source_hash,
            source_hash=unit.source_hash,
            crop_path=str(crop.relative_to(root)).replace("\\", "/"),
            crop_sha256=sha256_file(crop),
            provider="deepseek",
            model="deepseek-test",
            prompt_version="test-v1",
            pass_index=pass_index,
            classification=classification,
            latex=latex,
            source_markdown=markdown,
            equation_number=unit.equation_number,
            request_sha256=sha256_text("request-" + candidate_id),
            response_sha256=sha256_file(raw),
            raw_response_path=str(raw.relative_to(root)).replace("\\", "/"),
        )

    equation_a = candidate(
        "eq-a", equation, equation_crop, MathCandidateClassification.DISPLAY, "x = y + 1", None, 1
    )
    equation_b = candidate(
        "eq-b", equation, equation_crop, MathCandidateClassification.DISPLAY, "x=y+1", None, 2
    )
    inline_candidate = candidate(
        "in-a",
        inline,
        inline_crop,
        MathCandidateClassification.INLINE,
        None,
        "The value $x$ is known.",
        1,
    )
    write_jsonl(
        root / "evidence" / "math" / "candidates.jsonl", [equation_a, equation_b, inline_candidate]
    )
    write_json(
        root / "derived" / "verification.json",
        {
            "errors": [
                {
                    "code": "overlapping-units",
                    "page": 1,
                    "unit_id": equation.unit_id,
                    "other_unit_id": inline.unit_id,
                }
            ]
        },
    )
    return root, equation, inline, equation_a, inline_candidate


def _decision(
    root: Path,
    unit: SourceUnit,
    candidate: MathCandidate,
    page_image_hash: str,
    disposition: MathReviewDisposition = MathReviewDisposition.ACCEPTED,
    **updates: object,
) -> MathReviewDecision:
    values: dict[str, object] = {
        "decision_id": f"decision-{candidate.candidate_id}",
        "unit_id": unit.unit_id,
        "page": 1,
        "candidate_id": candidate.candidate_id,
        "disposition": disposition,
        "source_pdf_sha256": candidate.source_pdf_sha256,
        "source_hash": unit.source_hash,
        "crop_sha256": candidate.crop_sha256,
        "page_image_sha256": page_image_hash,
        "reviewed_against_pdf": True,
        "reviewer_id": "human",
        "reviewer_model": "codex",
        "reviewer_effort": "normal",
        "reason": "Compared crop and PDF page at high zoom.",
    }
    values.update(updates)
    return MathReviewDecision.model_validate(values)


def _manual_packet_decision(
    root: Path,
    unit: SourceUnit,
    *,
    disposition: MathReviewDisposition = MathReviewDisposition.MANUAL,
    include_unit_ids: list[str] | None = None,
) -> tuple[MathReviewDecision, Path, dict[str, object]]:
    result = build_math_review_packets(
        root,
        manual_only=True,
        output_root=Path("packets") / "math-manual-new",
        include_unit_ids=include_unit_ids,
    )
    summary = next(item for item in result["packets"] if unit.page in item["pages"])
    manifest_path = root / summary["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["units"] if item["unit"]["unit_id"] == unit.unit_id)
    page = next(item for item in manifest["pages"] if item["page"] == unit.page)
    decision = MathReviewDecision(
        decision_id=f"manual-{unit.unit_id}",
        unit_id=unit.unit_id,
        page=unit.page,
        candidate_id=None,
        packet_id=manifest["packet_id"],
        packet_sha256=manifest["packet_sha256"],
        manifest_sha256=manifest["manifest_sha256"],
        review_crop_path=entry["review_crop"]["packet_path"],
        disposition=disposition,
        source_pdf_sha256=manifest["source_pdf"]["sha256"],
        source_hash=unit.source_hash,
        crop_sha256=entry["review_crop"]["sha256"],
        page_image_sha256=page["image"]["sha256"],
        reviewed_against_pdf=True,
        reviewer_id="human",
        reviewer_model="codex",
        reviewer_effort="normal",
        reason="Compared the packet crop with the bound source PDF page at high zoom.",
        final_source_markdown=(
            unit.source_markdown
            if disposition is MathReviewDisposition.MANUAL and unit.kind is not UnitKind.EQUATION
            else None
        ),
        final_latex=(
            unit.latex
            if disposition is MathReviewDisposition.MANUAL and unit.kind is UnitKind.EQUATION
            else None
        ),
    )
    return decision, manifest_path, manifest


def _project_with_empty_packet_page(
    tmp_path: Path,
) -> tuple[Path, SourceUnit, SourceUnit, SourceUnit]:
    root, equation, inline, _, _ = _project(tmp_path)
    source = root / "source.pdf"
    replacement = root / "three-pages.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=220)
    page.insert_text((30, 60), "x = y + 1", fontsize=16)
    page.insert_text((30, 140), "The value $x$ is known.", fontsize=12)
    page = document.new_page(width=300, height=220)
    page.insert_text((30, 60), "No mathematics on this page.", fontsize=12)
    page = document.new_page(width=300, height=220)
    page.insert_text((30, 60), "z = 2", fontsize=16)
    document.save(replacement)
    document.close()
    source.unlink()
    replacement.replace(source)
    source_sha256 = sha256_file(source)
    write_yaml(
        root / "project.yaml",
        ProjectConfig(
            project_id="math-test",
            title="Math test",
            source_path=str(source),
            source_sha256=source_sha256,
            source_pages=3,
            profile="en-zh-cn",
        ).model_dump(mode="json", exclude_none=True),
    )
    third_crop = root / "derived" / "assets" / "third-equation.png"
    third_crop.write_bytes(b"third equation crop")
    third = SourceUnit(
        unit_id="p0003-u001-equation",
        kind=UnitKind.EQUATION,
        page=3,
        bbox=(20, 35, 180, 75),
        source_text="z = 2",
        source_hash=sha256_text("z = 2"),
        latex="z = 2",
        asset_refs=[
            {
                "kind": "equation",
                "path": "derived/assets/third-equation.png",
                "bbox": (20, 35, 180, 75),
            }
        ],
        confidence=0.7,
    )
    write_jsonl(root / "derived" / "units.jsonl", [equation, inline, third])
    return root, equation, inline, third


def _rewrite_manual_packet(
    manifest_path: Path, manifest: dict[str, object]
) -> tuple[Path, dict[str, object]]:
    packet_payload = {
        key: value
        for key, value in manifest.items()
        if key not in {"packet_id", "packet_sha256", "manifest_sha256"}
    }
    packet_sha256 = _canonical_sha256(packet_payload)
    packet_id = f"math-review-{packet_sha256[:20]}"
    manifest_core = {
        **packet_payload,
        "packet_id": packet_id,
        "packet_sha256": packet_sha256,
    }
    rewritten = {
        **manifest_core,
        "manifest_sha256": _canonical_sha256(manifest_core),
    }
    old_packet_root = manifest_path.parent.parent
    new_packet_root = old_packet_root.parent / packet_id
    shutil.copytree(old_packet_root, new_packet_root)
    rewritten_path = new_packet_root / "packet" / "manifest.json"
    rewritten_path.write_text(json.dumps(rewritten), encoding="utf-8")
    return rewritten_path, rewritten


def _structural_packet(
    root: Path,
    packet_id: str,
    units: list[SourceUnit],
    page_image_hash: str,
) -> str:
    payload = {
        "schema_version": 1,
        "kind": "test-math-review-packet",
        "packet_id": packet_id,
        "source_pdf": {
            "project_path": "source.pdf",
            "sha256": sha256_file(root / "source.pdf"),
        },
        "units": [
            {
                "source_unit": unit.model_dump(mode="json"),
                "source_hash": unit.source_hash,
                "full_page_image": {
                    "project_path": f"derived/verification/page-{unit.page:04d}.png",
                    "sha256": page_image_hash,
                },
            }
            for unit in units
        ],
    }
    payload_sha256 = _canonical_sha256(payload)
    manifest = {"payload": payload, "payload_sha256": payload_sha256}
    manifest_path = root / "packets" / "math-pilot" / packet_id / "packet" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload_sha256


def _canonical_sha256(value: object) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _structural_override(
    decision: MathReviewDecision,
    packet_id: str,
    packet_payload_sha256: str,
    **updates: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 5,
        "packet_id": packet_id,
        "packet_payload_sha256": packet_payload_sha256,
        "decision_id": decision.decision_id,
        "unit_id": decision.unit_id,
        "page": decision.page,
        "source_pdf_sha256": decision.source_pdf_sha256,
        "source_hash": decision.source_hash,
        "page_image_sha256": decision.page_image_sha256,
        "action": "ignore",
        "target_unit_ids": [],
        "fields": [],
        "final_values": {},
        "reviewed_against_pdf": True,
        "reviewer_id": decision.reviewer_id,
        "reviewer_model": decision.reviewer_model,
        "reviewer_effort": decision.reviewer_effort,
        "reviewer_task": decision.reviewer_task,
        "reason": "PDF page and crop show this is an overlapping non-independent fragment.",
        "reviewed_at": decision.reviewed_at,
    }
    value.update(updates)
    value["canonical_override_sha256"] = canonical_math_structural_override_sha256(value)
    return value


def _write_sidecar(
    path: Path, packet_id: str, packet_payload_sha256: str, overrides: list[dict[str, object]]
) -> None:
    write_yaml(
        path,
        {
            "schema_version": 5,
            "kind": "math-structural-review-sidecar",
            "packet_id": packet_id,
            "packet_payload_sha256": packet_payload_sha256,
            "overrides": overrides,
        },
    )


def _apply_manual_repair_chain(
    tmp_path: Path,
) -> tuple[Path, SourceUnit, SourceUnit, MathReviewDecision, MathReviewDecision]:
    root, equation, inline, _, _ = _project(tmp_path)
    primary, _, _ = _manual_packet_decision(root, equation)
    primary_file = tmp_path / "primary-manual.jsonl"
    primary_file.write_text(primary.model_dump_json() + "\n", encoding="utf-8")
    import_math_review(root, primary_file, confirm_visual_review=True)
    first = {unit.unit_id: unit for unit in apply_layout_overrides(root)}

    repaired_unit = first[equation.unit_id]
    repair, _, _ = _manual_packet_decision(
        root,
        repaired_unit,
        include_unit_ids=[repaired_unit.unit_id],
    )
    repair = repair.model_copy(
        update={
            "decision_id": f"repair-{repaired_unit.unit_id}",
            "final_latex": "x = y + 2",
            "reason": "A later PDF-bound repair corrects the terminal equation representation.",
        }
    )
    repair_file = tmp_path / "repair-manual.jsonl"
    repair_file.write_text(repair.model_dump_json() + "\n", encoding="utf-8")
    import_math_review(root, repair_file, confirm_visual_review=True)
    second = {unit.unit_id: unit for unit in apply_layout_overrides(root)}
    assert second[equation.unit_id].latex == "x = y + 2"
    return root, equation, inline, primary, repair


def test_report_is_local_and_contains_page_candidates_mathml_and_overlaps(tmp_path: Path) -> None:
    root, _, _, _, _ = _project(tmp_path)
    result = build_math_review_report(root)
    report = Path(result["report_path"])
    text = report.read_text(encoding="utf-8")
    assert report == root / "derived" / "math-review-report.html"
    assert "verification/page-0001.png" in text
    assert "Candidate A" in text and "Candidate B" in text
    assert "<math" in text
    assert "overlapping-units" in text or "p0001-u001-equation" in text
    assert "cdn" not in text.casefold()
    assert "<script src" not in text.casefold()
    assert (root / "derived" / "verification" / "page-0001.png").is_file()


def test_report_visibly_marks_stale_candidate_evidence(tmp_path: Path) -> None:
    root, _, _, equation_candidate, _ = _project(tmp_path)
    (root / equation_candidate.raw_response_path).write_text("tampered", encoding="utf-8")
    report = Path(build_math_review_report(root)["report_path"])
    text = report.read_text(encoding="utf-8")
    assert "Stale/invalid evidence" in text
    assert "candidate response hash is stale" in text


@pytest.mark.parametrize("payload", ["{not-json", "[]"])
def test_report_surfaces_malformed_verification_evidence(tmp_path: Path, payload: str) -> None:
    root, _, _, _, _ = _project(tmp_path)
    report = root / "derived" / "math-review-report.html"
    (root / "derived" / "verification.json").write_text(payload, encoding="utf-8")

    result = build_math_review_report(root)
    text = report.read_text(encoding="utf-8")

    assert result["verification_blocker"]
    assert "Verification evidence blocker" in text
    assert "Invalid verification evidence" in text


def test_import_requires_confirmation_and_rejects_stale_crop(tmp_path: Path) -> None:
    root, equation, _, equation_candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_image_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(root, equation, equation_candidate, page_image_hash)
    input_file = tmp_path / "decisions.jsonl"
    input_file.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="confirm_visual_review"):
        import_math_review(root, input_file)
    (root / equation_candidate.crop_path).write_bytes(b"changed crop")
    with pytest.raises(ValueError, match="stale"):
        import_math_review(root, input_file, confirm_visual_review=True)
    assert not (root / "overrides" / "layout.yaml").exists()
    assert not (root / "evidence" / "math" / "reviews.jsonl").exists()


@pytest.mark.parametrize(
    ("record_name", "field"),
    [
        ("candidate", "source_pdf_sha256"),
        ("candidate", "source_hash"),
        ("candidate", "crop_sha256"),
        ("candidate", "request_sha256"),
        ("candidate", "response_sha256"),
        ("decision", "source_pdf_sha256"),
        ("decision", "source_hash"),
        ("decision", "crop_sha256"),
        ("decision", "page_image_sha256"),
    ],
)
@pytest.mark.parametrize("invalid_digest", ["a" * 63, "g" * 64])
def test_math_evidence_models_require_strict_sha256_digests(
    tmp_path: Path,
    record_name: str,
    field: str,
    invalid_digest: str,
) -> None:
    root, equation, _, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(root, equation, candidate, page_hash)
    model = MathCandidate if record_name == "candidate" else MathReviewDecision
    record = candidate if record_name == "candidate" else decision
    valid_payload = record.model_dump(mode="json")
    assert model.model_validate(valid_payload) == record

    invalid_payload = {**valid_payload, field: invalid_digest}
    with pytest.raises(ValueError, match=field):
        model.model_validate(invalid_payload)


def test_math_decision_replay_is_idempotent_but_conflicts_are_strict(
    tmp_path: Path,
) -> None:
    root, equation, _, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(root, equation, candidate, page_hash)
    decisions = tmp_path / "replay-decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")

    first = import_math_review(root, decisions, confirm_visual_review=True)
    reviews = root / "evidence" / "math" / "reviews.jsonl"
    layout = root / "overrides" / "layout.yaml"
    apply_layout_overrides(root)
    units = root / "derived" / "units.jsonl"
    durable_before = {
        reviews: reviews.read_bytes(),
        layout: layout.read_bytes(),
        units: units.read_bytes(),
    }
    second = import_math_review(root, decisions, confirm_visual_review=True)

    assert first["recorded_count"] == 1
    assert second["recorded_count"] == 0
    assert second["outcomes"] == [
        {"decision_id": decision.decision_id, "status": "already-recorded"}
    ]
    assert {path: path.read_bytes() for path in durable_before} == durable_before

    conflict = decision.model_copy(
        update={"reason": "A different PDF comparison conclusion for the same decision ID."}
    )
    decisions.write_text(conflict.model_dump_json() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Strict math review decision_id conflict"):
        import_math_review(root, decisions, confirm_visual_review=True)
    assert {path: path.read_bytes() for path in durable_before} == durable_before


def test_guarded_math_override_rejects_stale_unit_before_any_apply_mutation(
    tmp_path: Path,
) -> None:
    root, equation, inline, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(root, equation, candidate, page_hash)
    decisions = tmp_path / "guarded-decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    import_math_review(root, decisions, confirm_visual_review=True)

    layout_path = root / "overrides" / "layout.yaml"
    layout = yaml.safe_load(layout_path.read_text(encoding="utf-8"))
    guard = layout["overrides"][0]["math_review_guard"]
    assert guard["decision_id"] == decision.decision_id
    assert guard["source_pdf_sha256"] == decision.source_pdf_sha256
    assert guard["source_hash"] == equation.source_hash

    changed = equation.model_copy(
        update={
            "source_text": "x = y + 2",
            "source_hash": sha256_text("x = y + 2"),
        }
    )
    units_path = root / "derived" / "units.jsonl"
    write_jsonl(units_path, [changed, inline])
    units_before = units_path.read_bytes()
    layout_before = layout_path.read_bytes()

    with pytest.raises(ValueError, match="stale or invalid|SourceUnit state is stale"):
        apply_layout_overrides(root)

    assert units_path.read_bytes() == units_before
    assert layout_path.read_bytes() == layout_before


def test_existing_flat_math_review_override_remains_applicable(tmp_path: Path) -> None:
    root, equation, _, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(root, equation, candidate, page_hash)
    decisions = tmp_path / "legacy-flat-decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    import_math_review(root, decisions, confirm_visual_review=True)
    layout_path = root / "overrides" / "layout.yaml"
    layout = yaml.safe_load(layout_path.read_text(encoding="utf-8"))
    layout["overrides"][0].pop("math_review_guard")
    write_yaml(layout_path, layout)

    applied = {unit.unit_id: unit for unit in apply_layout_overrides(root)}

    assert applied[equation.unit_id].verification_status is SemanticStatus.VERIFIED


def test_pre_receipt_flat_manual_result_rejects_representation_mutation(tmp_path: Path) -> None:
    root, equation, _, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(root, equation, candidate, page_hash)
    decisions = tmp_path / "legacy-flat-manual.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    import_math_review(root, decisions, confirm_visual_review=True)
    layout_path = root / "overrides" / "layout.yaml"
    layout = yaml.safe_load(layout_path.read_text(encoding="utf-8"))
    layout["overrides"][0].pop("math_review_guard")
    write_yaml(layout_path, layout)
    apply_layout_overrides(root)
    (root / "evidence" / "math" / "applied-overrides.jsonl").unlink()
    current = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    mutated = [
        unit.model_copy(update={"latex": "x = y + 999"})
        if unit.unit_id == equation.unit_id
        else unit
        for unit in current
    ]
    write_jsonl(root / "derived" / "units.jsonl", mutated)

    with pytest.raises(ValueError, match="current SourceUnit state is stale"):
        apply_layout_overrides(root)


def test_every_duplicate_guarded_entry_is_preflighted_before_merge(tmp_path: Path) -> None:
    root, equation, _, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(root, equation, candidate, page_hash)
    decisions = tmp_path / "duplicate-guard-decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    import_math_review(root, decisions, confirm_visual_review=True)
    layout_path = root / "overrides" / "layout.yaml"
    layout = yaml.safe_load(layout_path.read_text(encoding="utf-8"))
    current = layout["overrides"][0]
    stale = json.loads(json.dumps(current))
    stale["math_review_guard"]["unit_guard_sha256"] = "0" * 64
    layout["overrides"] = [stale, current]
    write_yaml(layout_path, layout)
    units_path = root / "derived" / "units.jsonl"
    units_before = units_path.read_bytes()

    with pytest.raises(ValueError, match="SourceUnit state is stale"):
        apply_layout_overrides(root)

    assert units_path.read_bytes() == units_before


def test_import_rejects_duplicate_current_unit_ids_before_staging(tmp_path: Path) -> None:
    root, equation, inline, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(root, equation, candidate, page_hash)
    decisions = tmp_path / "duplicate-unit-decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    write_jsonl(root / "derived" / "units.jsonl", [equation, equation, inline])

    with pytest.raises(ValueError, match="unique current SourceUnit IDs"):
        import_math_review(root, decisions, confirm_visual_review=True)

    assert not (root / "overrides" / "layout.yaml").exists()
    assert not (root / "evidence" / "math" / "reviews.jsonl").exists()


def test_rejected_and_uncertain_decisions_are_receipted_without_overrides(tmp_path: Path) -> None:
    root, equation, _, equation_candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_image_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    rejected = _decision(
        root,
        equation,
        equation_candidate,
        page_image_hash,
        MathReviewDisposition.REJECTED,
    )
    uncertain = _decision(
        root,
        equation,
        equation_candidate,
        page_image_hash,
        MathReviewDisposition.CORRECTED,
        decision_id="decision-uncertain",
        final_latex="x = y + 1",
        uncertainties=["subscript is unclear"],
    )
    input_file = tmp_path / "decisions.jsonl"
    input_file.write_text(
        rejected.model_dump_json() + "\n" + uncertain.model_dump_json() + "\n", encoding="utf-8"
    )
    result = import_math_review(root, input_file, confirm_visual_review=True)
    assert result["recorded_count"] == 2
    assert result["override_count"] == 0
    assert result["rejected_count"] == 1
    assert result["unresolved_count"] == 1
    assert (
        len((root / "evidence" / "math" / "reviews.jsonl").read_text(encoding="utf-8").splitlines())
        == 2
    )
    assert not (root / "overrides" / "layout.yaml").exists() or "unit_id" not in (
        root / "overrides" / "layout.yaml"
    ).read_text(encoding="utf-8")


def test_import_atomically_merges_layout_keeps_units_and_separates_equation_number(
    tmp_path: Path,
) -> None:
    root, equation, inline, equation_candidate, inline_candidate = _project(tmp_path)
    write_yaml(
        root / "overrides" / "layout.yaml",
        {"metadata": "keep", "overrides": [{"unit_id": "other", "reason": "existing"}]},
    )
    units_before = (root / "derived" / "units.jsonl").read_bytes()
    build_math_review_report(root)
    page_image_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    display = _decision(
        root,
        equation,
        equation_candidate,
        page_image_hash,
        final_latex="\\frac{x}{1} = y + 1",
        equation_number="9",
    )
    inline_decision = _decision(
        root,
        inline,
        inline_candidate,
        page_image_hash,
        decision_id="decision-inline",
        final_source_markdown="The value $x$ is known.",
    )
    input_file = tmp_path / "decisions.jsonl"
    input_file.write_text(
        display.model_dump_json() + "\n" + inline_decision.model_dump_json() + "\n",
        encoding="utf-8",
    )
    result = import_math_review(root, input_file, confirm_visual_review=True)
    assert result["override_count"] == 2
    payload = yaml.safe_load((root / "overrides" / "layout.yaml").read_text(encoding="utf-8"))
    assert payload["metadata"] == "keep"
    overrides = payload["overrides"]
    equation_override = next(item for item in overrides if item.get("unit_id") == equation.unit_id)
    inline_override = next(item for item in overrides if item.get("unit_id") == inline.unit_id)
    assert equation_override["verified"] is True
    assert equation_override["latex"] == r"\frac{x}{1} = y + 1"
    assert equation_override["equation_number"] == "9"
    assert "source_markdown" not in equation_override
    assert inline_override["source_markdown"] == "The value $x$ is known."
    assert "latex" not in inline_override
    assert "equation_number" not in inline_override
    assert (root / "derived" / "units.jsonl").read_bytes() == units_before

    # The importer only stages reviewed structural changes.  The existing
    # serial main-flow mutation consumes the merged layout.yaml afterwards.
    applied = {unit.unit_id: unit for unit in apply_layout_overrides(root)}
    assert applied[equation.unit_id].latex == r"\frac{x}{1} = y + 1"
    assert applied[equation.unit_id].equation_number == "9"
    assert applied[inline.unit_id].source_markdown == "The value $x$ is known."


def test_packet_bound_manual_inline_without_asset_refs_imports(tmp_path: Path) -> None:
    root, equation, inline, _, _ = _project(tmp_path)
    inline_without_assets = inline.model_copy(update={"asset_refs": []})
    write_jsonl(root / "derived" / "units.jsonl", [equation, inline_without_assets])
    decision, _, _ = _manual_packet_decision(root, inline_without_assets)
    (root / "evidence" / "math" / "candidates.jsonl").write_text(
        "deliberately invalid and irrelevant to manual review\n", encoding="utf-8"
    )
    decisions = tmp_path / "manual-decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")

    result = import_math_review(root, decisions, confirm_visual_review=True)

    assert result["recorded_count"] == 1
    assert result["override_count"] == 1
    receipt = MathReviewDecision.model_validate_json(
        (root / "evidence" / "math" / "reviews.jsonl").read_text(encoding="utf-8")
    )
    assert receipt.packet_id == decision.packet_id
    assert receipt.review_crop_path == decision.review_crop_path
    layout = yaml.safe_load((root / "overrides" / "layout.yaml").read_text(encoding="utf-8"))
    override = next(item for item in layout["overrides"] if item["unit_id"] == inline.unit_id)
    assert override["source_markdown"] == inline.source_markdown


@pytest.mark.parametrize(
    "attack",
    [
        "traversal",
        "wrong-packet",
        "tampered-crop",
        "tampered-manifest",
        "verified-injection",
    ],
)
def test_packet_bound_manual_review_rejects_packet_attacks(
    tmp_path: Path,
    attack: str,
) -> None:
    root, equation, inline, _, _ = _project(tmp_path)
    inline_without_assets = inline.model_copy(update={"asset_refs": []})
    write_jsonl(root / "derived" / "units.jsonl", [equation, inline_without_assets])
    decision, manifest_path, raw_manifest = _manual_packet_decision(
        root, inline_without_assets, disposition=MathReviewDisposition.REJECTED
    )
    manifest = dict(raw_manifest)

    if attack == "traversal":
        decision = decision.model_copy(update={"review_crop_path": "../manifest.json"})
    elif attack == "wrong-packet":
        decision = decision.model_copy(update={"packet_id": "math-review-wrong-packet"})
    elif attack == "tampered-crop":
        entry = next(
            item
            for item in manifest["units"]  # type: ignore[index]
            if item["unit"]["unit_id"] == inline.unit_id
        )
        crop = manifest_path.parent / entry["review_crop"]["packet_path"]
        crop.write_bytes(b"swapped crop")
    elif attack == "tampered-manifest":
        manifest["math_unit_count"] = 999
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        entries = manifest["units"]
        assert isinstance(entries, list)
        entry = next(item for item in entries if item["unit"]["unit_id"] == inline.unit_id)
        entry["verified"] = True
        packet_payload = {
            key: value
            for key, value in manifest.items()
            if key not in {"packet_id", "packet_sha256", "manifest_sha256"}
        }
        manifest["packet_sha256"] = _canonical_sha256(packet_payload)
        manifest_core = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        manifest["manifest_sha256"] = _canonical_sha256(manifest_core)
        decision = decision.model_copy(
            update={
                "packet_sha256": manifest["packet_sha256"],
                "manifest_sha256": manifest["manifest_sha256"],
            }
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    decisions = tmp_path / f"{attack}.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stale|invalid|packet|crop|path|verified"):
        import_math_review(root, decisions, confirm_visual_review=True)
    assert not (root / "overrides" / "layout.yaml").exists()
    assert not (root / "evidence" / "math" / "reviews.jsonl").exists()


def test_structural_sidecar_import_is_packet_bound_and_cannot_elevate_status(
    tmp_path: Path,
) -> None:
    root, equation, _, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(
        root,
        equation,
        candidate,
        page_hash,
        MathReviewDisposition.REJECTED,
        reviewer_task="structural-test",
    )
    decisions = tmp_path / "decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    packet_id = "structural-test-packet"
    packet_hash = _structural_packet(root, packet_id, [equation], page_hash)
    sidecar = tmp_path / "structural.yaml"
    _write_sidecar(
        sidecar,
        packet_id,
        packet_hash,
        [_structural_override(decision, packet_id, packet_hash)],
    )

    result = import_math_review(
        root,
        decisions,
        confirm_visual_review=True,
        structural_file=sidecar,
    )
    assert result["structural_count"] == 1
    assert result["structural_layout_override_count"] == 1
    layout = yaml.safe_load((root / "overrides" / "layout.yaml").read_text(encoding="utf-8"))
    imported = layout["overrides"][0]
    assert imported["unit_id"] == equation.unit_id
    assert imported["ignore"] is True
    assert imported["reason"] == (
        "PDF page and crop show this is an overlapping non-independent fragment."
    )
    assert imported["math_review_decision_id"] == decision.decision_id
    assert imported["math_review_guard"]["unit_guard_sha256"]
    serialized = json.dumps(imported)
    assert '"verified"' not in serialized
    assert '"status"' not in serialized
    assert "insert_after" not in serialized
    assert (root / "evidence" / "math" / "structural-reviews.jsonl").is_file()


def test_structural_review_replay_is_idempotent_but_conflicts_are_strict(
    tmp_path: Path,
) -> None:
    root, equation, _, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(
        root,
        equation,
        candidate,
        page_hash,
        MathReviewDisposition.REJECTED,
        reviewer_task="structural-replay-test",
    )
    decisions = tmp_path / "structural-replay-decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    packet_id = "structural-replay-packet"
    packet_hash = _structural_packet(root, packet_id, [equation], page_hash)
    override = _structural_override(decision, packet_id, packet_hash)
    sidecar = tmp_path / "structural-replay.yaml"
    _write_sidecar(sidecar, packet_id, packet_hash, [override])

    first = import_math_review(
        root,
        decisions,
        confirm_visual_review=True,
        structural_file=sidecar,
    )
    apply_layout_overrides(root)
    reviews = root / "evidence" / "math" / "reviews.jsonl"
    structural_reviews = root / "evidence" / "math" / "structural-reviews.jsonl"
    layout = root / "overrides" / "layout.yaml"
    durable_before = {
        reviews: reviews.read_bytes(),
        structural_reviews: structural_reviews.read_bytes(),
        layout: layout.read_bytes(),
        root / "derived" / "units.jsonl": (root / "derived" / "units.jsonl").read_bytes(),
    }
    second = import_math_review(
        root,
        decisions,
        confirm_visual_review=True,
        structural_file=sidecar,
    )

    assert first["structural_count"] == 1
    assert second["recorded_count"] == 0
    assert second["structural_count"] == 0
    assert {path: path.read_bytes() for path in durable_before} == durable_before

    conflicting_override = dict(override)
    conflicting_override["reason"] = "A different structural conclusion for the same decision ID."
    conflicting_override["canonical_override_sha256"] = canonical_math_structural_override_sha256(
        conflicting_override
    )
    _write_sidecar(sidecar, packet_id, packet_hash, [conflicting_override])
    with pytest.raises(ValueError, match="Strict structural math review decision_id conflict"):
        import_math_review(
            root,
            decisions,
            confirm_visual_review=True,
            structural_file=sidecar,
        )
    assert {path: path.read_bytes() for path in durable_before} == durable_before


def test_revised_structural_merge_does_not_stage_a_duplicate_removal(
    tmp_path: Path,
) -> None:
    root, equation, inline, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    packet_id = "revised-structural-merge-packet"
    packet_hash = _structural_packet(root, packet_id, [equation, inline], page_hash)

    results: list[dict[str, object]] = []
    for suffix in ("first", "revision"):
        decision = _decision(
            root,
            equation,
            candidate,
            page_hash,
            MathReviewDisposition.REJECTED,
            decision_id=f"revised-merge-{suffix}",
            reviewer_task=f"revised-merge-{suffix}",
        )
        decisions = tmp_path / f"revised-merge-{suffix}.jsonl"
        decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
        structural = _structural_override(
            decision,
            packet_id,
            packet_hash,
            action="merge",
            target_unit_ids=[inline.unit_id],
        )
        structural["canonical_override_sha256"] = canonical_math_structural_override_sha256(
            structural
        )
        sidecar = tmp_path / f"revised-merge-{suffix}.yaml"
        _write_sidecar(sidecar, packet_id, packet_hash, [structural])
        results.append(
            import_math_review(
                root,
                decisions,
                confirm_visual_review=True,
                structural_file=sidecar,
            )
        )

    assert results[0]["structural_layout_override_count"] == 2
    assert results[1]["structural_layout_override_count"] == 1
    revision_outcome = next(
        item
        for item in results[1]["outcomes"]
        if item["decision_id"] == "revised-merge-revision"
        and item["status"] == "structural-overridden"
    )
    assert revision_outcome["removal_already_staged"] is True

    layout = yaml.safe_load((root / "overrides" / "layout.yaml").read_text(encoding="utf-8"))
    removals = [
        override
        for override in layout["overrides"]
        if override.get("unit_id") == equation.unit_id and override.get("ignore") is True
    ]
    assert len(removals) == 1
    structural_reviews = read_jsonl(
        root / "evidence" / "math" / "structural-reviews.jsonl",
        MathStructuralOverrideDecision,
    )
    assert {review.decision_id for review in structural_reviews} == {
        "revised-merge-first",
        "revised-merge-revision",
    }

    applied = {unit.unit_id: unit for unit in apply_layout_overrides(root)}
    assert equation.unit_id not in applied
    assert inline.unit_id in applied


def test_consumed_structural_ignore_allows_later_guarded_page_work(
    tmp_path: Path,
) -> None:
    root, equation, inline, equation_candidate, inline_candidate = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    ignored = _decision(
        root,
        equation,
        equation_candidate,
        page_hash,
        MathReviewDisposition.REJECTED,
        reviewer_task="consumed-structural-test",
    )
    ignored_file = tmp_path / "ignored.jsonl"
    ignored_file.write_text(ignored.model_dump_json() + "\n", encoding="utf-8")
    packet_id = "consumed-structural-packet"
    packet_hash = _structural_packet(root, packet_id, [equation], page_hash)
    sidecar = tmp_path / "consumed-structural.yaml"
    _write_sidecar(
        sidecar,
        packet_id,
        packet_hash,
        [_structural_override(ignored, packet_id, packet_hash)],
    )
    import_math_review(
        root,
        ignored_file,
        confirm_visual_review=True,
        structural_file=sidecar,
    )
    first_applied = apply_layout_overrides(root)
    assert equation.unit_id not in {unit.unit_id for unit in first_applied}
    receipts = root / "evidence" / "math" / "applied-overrides.jsonl"
    assert receipts.is_file()

    later = _decision(root, inline, inline_candidate, page_hash)
    later_file = tmp_path / "later.jsonl"
    later_file.write_text(later.model_dump_json() + "\n", encoding="utf-8")
    import_math_review(root, later_file, confirm_visual_review=True)

    second_applied = {unit.unit_id: unit for unit in apply_layout_overrides(root)}
    assert equation.unit_id not in second_applied
    assert second_applied[inline.unit_id].verification_status is SemanticStatus.VERIFIED

    # A later re-extraction restores the consumed selector.  The absence
    # receipt must reject that state instead of silently applying it again.
    write_jsonl(
        root / "derived" / "units.jsonl",
        [equation, second_applied[inline.unit_id]],
    )
    with pytest.raises(ValueError, match="applied absent state is stale"):
        apply_layout_overrides(root)


def test_authenticated_manual_repair_chain_bootstraps_and_accepts_unrelated_work(
    tmp_path: Path,
) -> None:
    root, equation, inline, primary, repair = _apply_manual_repair_chain(tmp_path)
    assert primary.decision_id != repair.decision_id

    # Model an existing flat project whose chain predates durable chain receipts.
    (root / "evidence" / "math" / "applied-override-chains.jsonl").unlink()
    (root / "evidence" / "math" / "applied-overrides.jsonl").unlink()

    current_inline = next(
        unit
        for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
        if unit.unit_id == inline.unit_id
    )
    unrelated, _, _ = _manual_packet_decision(
        root,
        current_inline,
        include_unit_ids=[current_inline.unit_id],
    )
    unrelated_file = tmp_path / "unrelated-manual.jsonl"
    unrelated_file.write_text(unrelated.model_dump_json() + "\n", encoding="utf-8")
    import_math_review(root, unrelated_file, confirm_visual_review=True)

    applied = {unit.unit_id: unit for unit in apply_layout_overrides(root)}

    assert applied[equation.unit_id].latex == "x = y + 2"
    assert applied[inline.unit_id].verification_status is SemanticStatus.VERIFIED
    assert (root / "evidence" / "math" / "applied-override-chains.jsonl").is_file()


@pytest.mark.parametrize("mutation", ["tamper", "remove", "reorder"])
def test_authenticated_manual_repair_chain_rejects_terminal_history_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    root, equation, _, _, repair = _apply_manual_repair_chain(tmp_path)
    layout_path = root / "overrides" / "layout.yaml"
    layout = yaml.safe_load(layout_path.read_text(encoding="utf-8"))
    indexes = [
        index
        for index, override in enumerate(layout["overrides"])
        if override.get("unit_id") == equation.unit_id
    ]
    assert len(indexes) == 2
    if mutation == "tamper":
        layout["overrides"][indexes[-1]]["latex"] = "x = y + 999"
    elif mutation == "remove":
        layout["overrides"].pop(indexes[-1])
    else:
        first, second = indexes
        layout["overrides"][first], layout["overrides"][second] = (
            layout["overrides"][second],
            layout["overrides"][first],
        )
    write_yaml(layout_path, layout)
    units_before = (root / "derived" / "units.jsonl").read_bytes()

    with pytest.raises(ValueError, match="removed|reordered|altered|unauthenticated"):
        apply_layout_overrides(root)

    assert (root / "derived" / "units.jsonl").read_bytes() == units_before
    durable_repairs = [
        decision
        for decision in read_jsonl(root / "evidence" / "math" / "reviews.jsonl", MathReviewDecision)
        if decision.decision_id == repair.decision_id
    ]
    assert durable_repairs == [repair]


def test_authenticated_manual_repair_chain_rejects_stale_pdf_and_terminal_unit(
    tmp_path: Path,
) -> None:
    root, equation, _, _, _ = _apply_manual_repair_chain(tmp_path)
    units_path = root / "derived" / "units.jsonl"
    current = read_jsonl(units_path, SourceUnit)
    write_jsonl(
        units_path,
        [
            unit.model_copy(update={"bbox": (21.0, 35.0, 180.0, 75.0)})
            if unit.unit_id == equation.unit_id
            else unit
            for unit in current
        ],
    )
    with pytest.raises(ValueError, match="chain state.*stale"):
        apply_layout_overrides(root)

    write_jsonl(units_path, current)
    source = root / "source.pdf"
    source.write_bytes(source.read_bytes() + b"stale")
    with pytest.raises(ValueError, match="source PDF changed"):
        apply_layout_overrides(root)


def test_authenticated_chain_can_end_in_structural_ignore(tmp_path: Path) -> None:
    root, equation, _, _, _ = _apply_manual_repair_chain(tmp_path)
    current = next(
        unit
        for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
        if unit.unit_id == equation.unit_id
    )
    rejected, _, manifest = _manual_packet_decision(
        root,
        current,
        disposition=MathReviewDisposition.REJECTED,
        include_unit_ids=[current.unit_id],
    )
    rejected = rejected.model_copy(
        update={
            "decision_id": f"ignore-repair-{current.unit_id}",
            "reviewer_task": "terminal-ignore-repair",
        }
    )
    decisions = tmp_path / "terminal-ignore.jsonl"
    decisions.write_text(rejected.model_dump_json() + "\n", encoding="utf-8")
    packet_id = str(manifest["packet_id"])
    packet_hash = str(manifest["packet_sha256"])
    sidecar = tmp_path / "terminal-ignore.yaml"
    _write_sidecar(
        sidecar,
        packet_id,
        packet_hash,
        [_structural_override(rejected, packet_id, packet_hash)],
    )
    import_math_review(
        root,
        decisions,
        confirm_visual_review=True,
        structural_file=sidecar,
    )

    first = apply_layout_overrides(root)
    second = apply_layout_overrides(root)

    assert equation.unit_id not in {unit.unit_id for unit in first}
    assert equation.unit_id not in {unit.unit_id for unit in second}


def test_authenticated_chain_rejects_duplicate_decision_identity(tmp_path: Path) -> None:
    root, equation, _, _, _ = _apply_manual_repair_chain(tmp_path)
    layout_path = root / "overrides" / "layout.yaml"
    layout = yaml.safe_load(layout_path.read_text(encoding="utf-8"))
    repair_override = next(
        override
        for override in reversed(layout["overrides"])
        if override.get("unit_id") == equation.unit_id
    )
    layout["overrides"].append(dict(repair_override))
    write_yaml(layout_path, layout)

    with pytest.raises(ValueError, match="duplicate decision IDs"):
        apply_layout_overrides(root)


def test_applied_structural_receipt_rejects_pdf_and_exact_target_mutation(
    tmp_path: Path,
) -> None:
    root, equation, _, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(
        root,
        equation,
        candidate,
        page_hash,
        MathReviewDisposition.REJECTED,
        reviewer_task="consumed-reclassify-test",
    )
    decisions = tmp_path / "reclassify.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    packet_id = "consumed-reclassify-packet"
    packet_hash = _structural_packet(root, packet_id, [equation], page_hash)
    sidecar = tmp_path / "reclassify.yaml"
    _write_sidecar(
        sidecar,
        packet_id,
        packet_hash,
        [
            _structural_override(
                decision,
                packet_id,
                packet_hash,
                action="reclassify",
                fields=["kind"],
                final_values={"kind": "paragraph"},
            )
        ],
    )
    import_math_review(
        root,
        decisions,
        confirm_visual_review=True,
        structural_file=sidecar,
    )
    applied = {unit.unit_id: unit for unit in apply_layout_overrides(root)}
    assert applied[equation.unit_id].kind is UnitKind.PARAGRAPH

    source = root / "source.pdf"
    source_before = source.read_bytes()
    source.write_bytes(source_before + b"stale")
    with pytest.raises(ValueError, match="source PDF changed"):
        apply_layout_overrides(root)
    source.write_bytes(source_before)

    current = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    changed = [
        unit.model_copy(update={"bbox": (21.0, 35.0, 180.0, 75.0)})
        if unit.unit_id == equation.unit_id
        else unit
        for unit in current
    ]
    write_jsonl(root / "derived" / "units.jsonl", changed)
    with pytest.raises(ValueError, match="applied SourceUnit state is stale"):
        apply_layout_overrides(root)


def test_missing_never_applied_nested_structural_guard_is_rejected(tmp_path: Path) -> None:
    root, equation, inline, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(
        root,
        equation,
        candidate,
        page_hash,
        MathReviewDisposition.REJECTED,
        reviewer_task="never-applied-test",
    )
    decisions = tmp_path / "never-applied.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    packet_id = "never-applied-packet"
    packet_hash = _structural_packet(root, packet_id, [equation], page_hash)
    sidecar = tmp_path / "never-applied.yaml"
    _write_sidecar(
        sidecar,
        packet_id,
        packet_hash,
        [_structural_override(decision, packet_id, packet_hash)],
    )
    import_math_review(
        root,
        decisions,
        confirm_visual_review=True,
        structural_file=sidecar,
    )
    write_jsonl(root / "derived" / "units.jsonl", [inline])

    with pytest.raises(ValueError, match="requires exactly one current SourceUnit"):
        apply_layout_overrides(root)

    assert not (root / "evidence" / "math" / "applied-overrides.jsonl").exists()


def test_pre_receipt_flat_structural_result_is_bootstrapped_from_packet(
    tmp_path: Path,
) -> None:
    root, equation, inline, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(
        root,
        equation,
        candidate,
        page_hash,
        MathReviewDisposition.REJECTED,
        reviewer_task="legacy-consumed-test",
    )
    decisions = tmp_path / "legacy-consumed.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    packet_id = "legacy-consumed-packet"
    packet_hash = _structural_packet(root, packet_id, [equation, inline], page_hash)
    sidecar = tmp_path / "legacy-consumed.yaml"
    _write_sidecar(
        sidecar,
        packet_id,
        packet_hash,
        [
            _structural_override(
                decision,
                packet_id,
                packet_hash,
                action="merge",
                target_unit_ids=[inline.unit_id],
                fields=["source_markdown"],
                final_values={"source_markdown": "The merged value $x$ is known."},
            )
        ],
    )
    import_math_review(
        root,
        decisions,
        confirm_visual_review=True,
        structural_file=sidecar,
    )
    layout_path = root / "overrides" / "layout.yaml"
    layout = yaml.safe_load(layout_path.read_text(encoding="utf-8"))
    layout["overrides"][0].pop("math_review_guard")
    write_yaml(layout_path, layout)
    apply_layout_overrides(root)
    receipts = root / "evidence" / "math" / "applied-overrides.jsonl"
    receipts.unlink()

    reapplied = apply_layout_overrides(root)

    assert equation.unit_id not in {unit.unit_id for unit in reapplied}
    target = next(unit for unit in reapplied if unit.unit_id == inline.unit_id)
    assert target.source_markdown == "The merged value $x$ is known."
    assert receipts.is_file()


@pytest.mark.parametrize("action", ["reclassify", "merge"])
def test_pre_receipt_flat_structural_present_result_is_exactly_reconstructed(
    tmp_path: Path,
    action: str,
) -> None:
    root, equation, inline, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(
        root,
        equation,
        candidate,
        page_hash,
        MathReviewDisposition.REJECTED,
        reviewer_task=f"legacy-{action}-test",
    )
    decisions = tmp_path / f"legacy-{action}.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    packet_id = f"legacy-{action}-packet"
    packet_hash = _structural_packet(root, packet_id, [equation, inline], page_hash)
    structural_updates: dict[str, object]
    if action == "reclassify":
        structural_updates = {
            "action": "reclassify",
            "fields": ["kind"],
            "final_values": {"kind": "paragraph"},
        }
    else:
        structural_updates = {
            "action": "merge",
            "target_unit_ids": [inline.unit_id],
            "fields": ["source_markdown"],
            "final_values": {"source_markdown": "The merged value $x$ is known."},
        }
    sidecar = tmp_path / f"legacy-{action}.yaml"
    _write_sidecar(
        sidecar,
        packet_id,
        packet_hash,
        [
            _structural_override(
                decision,
                packet_id,
                packet_hash,
                **structural_updates,
            )
        ],
    )
    import_math_review(
        root,
        decisions,
        confirm_visual_review=True,
        structural_file=sidecar,
    )
    layout_path = root / "overrides" / "layout.yaml"
    layout = yaml.safe_load(layout_path.read_text(encoding="utf-8"))
    for override in layout["overrides"]:
        override.pop("math_review_guard")
    write_yaml(layout_path, layout)
    apply_layout_overrides(root)
    receipts = root / "evidence" / "math" / "applied-overrides.jsonl"
    receipts.unlink()

    reapplied = {unit.unit_id: unit for unit in apply_layout_overrides(root)}

    if action == "reclassify":
        assert reapplied[equation.unit_id].kind is UnitKind.PARAGRAPH
    else:
        assert equation.unit_id not in reapplied
        assert reapplied[inline.unit_id].source_markdown == "The merged value $x$ is known."
    assert receipts.is_file()


def test_structural_receipt_preserves_explicit_null_and_remains_reloadable(
    tmp_path: Path,
) -> None:
    root, equation, inline, equation_candidate, inline_candidate = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(
        root,
        equation,
        equation_candidate,
        page_hash,
        MathReviewDisposition.REJECTED,
        reviewer_task="explicit-null-structural-test",
    )
    decisions = tmp_path / "explicit-null-decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    packet_id = "explicit-null-packet"
    packet_hash = _structural_packet(root, packet_id, [equation], page_hash)
    sidecar = tmp_path / "explicit-null-structural.yaml"
    _write_sidecar(
        sidecar,
        packet_id,
        packet_hash,
        [
            _structural_override(
                decision,
                packet_id,
                packet_hash,
                action="reclassify",
                fields=["kind", "source_markdown", "latex"],
                final_values={
                    "kind": "paragraph",
                    "source_markdown": "x = y + 1",
                    "latex": None,
                },
            )
        ],
    )

    first = import_math_review(
        root,
        decisions,
        confirm_visual_review=True,
        structural_file=sidecar,
    )

    assert first["structural_count"] == 1
    ledger = root / "evidence" / "math" / "structural-reviews.jsonl"
    receipt = json.loads(ledger.read_text(encoding="utf-8"))
    assert receipt["final_values"] == {
        "kind": "paragraph",
        "source_markdown": "x = y + 1",
        "latex": None,
    }
    validated = MathStructuralOverrideDecision.model_validate(receipt)
    assert validated.canonical_override_sha256 == canonical_math_structural_override_sha256(receipt)

    unrelated = _decision(root, inline, inline_candidate, page_hash)
    unrelated_file = tmp_path / "unrelated-decisions.jsonl"
    unrelated_file.write_text(unrelated.model_dump_json() + "\n", encoding="utf-8")
    second = import_math_review(
        root,
        unrelated_file,
        confirm_visual_review=True,
    )
    assert second["recorded_count"] == 1
    assert second["structural_count"] == 0


def test_repair_structural_ledger_restores_only_hash_proven_declared_nulls(
    tmp_path: Path,
) -> None:
    root, equation, _, equation_candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(
        root,
        equation,
        equation_candidate,
        page_hash,
        MathReviewDisposition.REJECTED,
        reviewer_task="repair-explicit-null-test",
    )
    packet_id = "repair-explicit-null-packet"
    packet_hash = _structural_packet(root, packet_id, [equation], page_hash)
    override = _structural_override(
        decision,
        packet_id,
        packet_hash,
        action="reclassify",
        fields=["kind", "source_markdown", "latex"],
        final_values={
            "kind": "paragraph",
            "source_markdown": "x = y + 1",
            "latex": None,
        },
    )
    broken = json.loads(json.dumps(override))
    del broken["final_values"]["latex"]
    ledger = root / "evidence" / "math" / "structural-reviews.jsonl"
    ledger.write_text(json.dumps(broken) + "\n", encoding="utf-8")

    result = repair_math_structural_review_ledger(root)

    assert result["record_count"] == 1
    assert result["repaired_count"] == 1
    repaired = json.loads(ledger.read_text(encoding="utf-8"))
    assert repaired["final_values"]["latex"] is None
    MathStructuralOverrideDecision.model_validate(repaired)


def test_repair_structural_ledger_rejects_unproven_change_without_writing(
    tmp_path: Path,
) -> None:
    root, equation, _, equation_candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(
        root,
        equation,
        equation_candidate,
        page_hash,
        MathReviewDisposition.REJECTED,
        reviewer_task="reject-unproven-repair-test",
    )
    packet_id = "reject-unproven-repair-packet"
    packet_hash = _structural_packet(root, packet_id, [equation], page_hash)
    broken = _structural_override(
        decision,
        packet_id,
        packet_hash,
        action="reclassify",
        fields=["kind", "latex"],
        final_values={"kind": "paragraph", "latex": None},
    )
    del broken["final_values"]["latex"]
    broken["canonical_override_sha256"] = "0" * 64
    ledger = root / "evidence" / "math" / "structural-reviews.jsonl"
    ledger.write_text(json.dumps(broken) + "\n", encoding="utf-8")
    original = ledger.read_bytes()

    with pytest.raises(ValueError, match="Refusing.*canonical_override_sha256"):
        repair_math_structural_review_ledger(root)

    assert ledger.read_bytes() == original


def test_manual_packet_structural_sidecar_uses_validated_2x_packet_page(
    tmp_path: Path,
) -> None:
    root, equation, _, _, _ = _project(tmp_path)
    build_math_review_report(root)
    verification_page = root / "derived" / "verification" / "page-0001.png"
    verification_hash = sha256_file(verification_page)
    decision, manifest_path, manifest = _manual_packet_decision(
        root, equation, disposition=MathReviewDisposition.REJECTED
    )
    decision = decision.model_copy(update={"reviewer_task": "manual-structural-test"})
    packet_page = next(item for item in manifest["pages"] if item["page"] == 1)["image"]
    packet_page_path = manifest_path.parent / packet_page["packet_path"]
    assert fitz.Pixmap(str(verification_page)).width == 450
    assert fitz.Pixmap(str(packet_page_path)).width == 600
    assert verification_hash != decision.page_image_sha256

    decisions = tmp_path / "manual-structural-decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    assert decision.packet_id is not None
    assert decision.packet_sha256 is not None
    sidecar = tmp_path / "manual-structural.yaml"
    _write_sidecar(
        sidecar,
        decision.packet_id,
        decision.packet_sha256,
        [
            _structural_override(
                decision,
                decision.packet_id,
                decision.packet_sha256,
            )
        ],
    )

    result = import_math_review(
        root,
        decisions,
        confirm_visual_review=True,
        structural_file=sidecar,
    )

    assert result["structural_count"] == 1
    assert result["structural_layout_override_count"] == 1


def test_explicit_verified_paragraph_packet_passes_strict_structural_import(
    tmp_path: Path,
) -> None:
    root, equation, inline, _, _ = _project(tmp_path)
    verified_paragraph = inline.model_copy(
        update={
            "source_text": "This paragraph continues into the next unit.",
            "source_markdown": "This paragraph continues into the next unit.",
            "source_hash": sha256_text("This paragraph continues into the next unit."),
            "asset_refs": [],
            "math_status": SemanticStatus.VERIFIED,
        }
    )
    write_jsonl(root / "derived" / "units.jsonl", [equation, verified_paragraph])
    decision, _, manifest = _manual_packet_decision(
        root,
        verified_paragraph,
        disposition=MathReviewDisposition.REJECTED,
        include_unit_ids=[verified_paragraph.unit_id],
    )
    decision = decision.model_copy(update={"reviewer_task": "verified-paragraph-structure"})
    assert manifest["explicit_unit_ids"] == [verified_paragraph.unit_id]
    assert decision.packet_id is not None
    assert decision.packet_sha256 is not None
    decisions = tmp_path / "verified-paragraph-decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    sidecar = tmp_path / "verified-paragraph-structural.yaml"
    _write_sidecar(
        sidecar,
        decision.packet_id,
        decision.packet_sha256,
        [
            _structural_override(
                decision,
                decision.packet_id,
                decision.packet_sha256,
            )
        ],
    )

    result = import_math_review(
        root,
        decisions,
        confirm_visual_review=True,
        structural_file=sidecar,
    )

    assert result["structural_count"] == 1
    assert result["structural_layout_override_count"] == 1


@pytest.mark.parametrize("attack", ["packet-page", "other-unit-crop"])
def test_manual_packet_structural_sidecar_rejects_tampered_packet_evidence(
    tmp_path: Path, attack: str
) -> None:
    root, equation, inline, _, _ = _project(tmp_path)
    build_math_review_report(root)
    decision, manifest_path, manifest = _manual_packet_decision(
        root, equation, disposition=MathReviewDisposition.REJECTED
    )
    decision = decision.model_copy(update={"reviewer_task": "manual-tamper-test"})
    if attack == "packet-page":
        binding = next(item for item in manifest["pages"] if item["page"] == 1)["image"]
    else:
        binding = next(
            item for item in manifest["units"] if item["unit"]["unit_id"] == inline.unit_id
        )["review_crop"]
    (manifest_path.parent / binding["packet_path"]).write_bytes(b"tampered")

    decisions = tmp_path / f"manual-structural-{attack}.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    assert decision.packet_id is not None
    assert decision.packet_sha256 is not None
    sidecar = tmp_path / f"manual-structural-{attack}.yaml"
    _write_sidecar(
        sidecar,
        decision.packet_id,
        decision.packet_sha256,
        [
            _structural_override(
                decision,
                decision.packet_id,
                decision.packet_sha256,
            )
        ],
    )

    with pytest.raises(ValueError, match="page image|review crop|tampered"):
        import_math_review(
            root,
            decisions,
            confirm_visual_review=True,
            structural_file=sidecar,
        )


def test_manual_structural_packet_accepts_consecutive_empty_page(tmp_path: Path) -> None:
    root, equation, _, _ = _project_with_empty_packet_page(tmp_path)
    decision, _, manifest = _manual_packet_decision(
        root, equation, disposition=MathReviewDisposition.REJECTED
    )
    empty_page = next(item for item in manifest["pages"] if item["page"] == 2)
    assert empty_page["review_unit_ids"] == []
    decision = decision.model_copy(update={"reviewer_task": "empty-page-test"})
    decisions = tmp_path / "empty-page-decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    assert decision.packet_id is not None
    assert decision.packet_sha256 is not None
    sidecar = tmp_path / "empty-page-structural.yaml"
    _write_sidecar(
        sidecar,
        decision.packet_id,
        decision.packet_sha256,
        [_structural_override(decision, decision.packet_id, decision.packet_sha256)],
    )

    result = import_math_review(
        root, decisions, confirm_visual_review=True, structural_file=sidecar
    )

    assert result["structural_count"] == 1


@pytest.mark.parametrize("attack", ["empty-extra", "formula-missing", "wrong-order"])
def test_manual_structural_packet_rejects_invalid_page_unit_sequence(
    tmp_path: Path, attack: str
) -> None:
    root, equation, inline, third = _project_with_empty_packet_page(tmp_path)
    decision, manifest_path, raw_manifest = _manual_packet_decision(
        root, equation, disposition=MathReviewDisposition.REJECTED
    )
    manifest = json.loads(json.dumps(raw_manifest))
    pages = {item["page"]: item for item in manifest["pages"]}
    if attack == "empty-extra":
        pages[2]["review_unit_ids"] = [third.unit_id]
    elif attack == "formula-missing":
        pages[1]["review_unit_ids"].remove(inline.unit_id)
    else:
        pages[1]["review_unit_ids"].reverse()
    _, manifest = _rewrite_manual_packet(manifest_path, manifest)
    decision = decision.model_copy(
        update={
            "packet_id": manifest["packet_id"],
            "packet_sha256": manifest["packet_sha256"],
            "manifest_sha256": manifest["manifest_sha256"],
            "reviewer_task": "page-unit-sequence-test",
        }
    )
    decisions = tmp_path / f"{attack}-decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    sidecar = tmp_path / f"{attack}-structural.yaml"
    _write_sidecar(
        sidecar,
        str(manifest["packet_id"]),
        str(manifest["packet_sha256"]),
        [
            _structural_override(
                decision,
                str(manifest["packet_id"]),
                str(manifest["packet_sha256"]),
            )
        ],
    )

    with pytest.raises(ValueError, match="page.*review unit|uniquely bind review unit"):
        import_math_review(root, decisions, confirm_visual_review=True, structural_file=sidecar)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda sidecar: sidecar["overrides"][0].update({"verified": True}), "extra"),
        (
            lambda sidecar: sidecar["overrides"][0].update({"canonical_override_sha256": "0" * 64}),
            "canonical_override_sha256",
        ),
        (lambda sidecar: sidecar.update({"packet_payload_sha256": "0" * 64}), "packet"),
        (lambda sidecar: sidecar.update({"packet_id": "../escape"}), "packet_id"),
    ],
)
def test_structural_sidecar_rejects_injection_replacement_hash_and_traversal(
    tmp_path: Path,
    mutation: object,
    message: str,
) -> None:
    root, equation, _, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(
        root,
        equation,
        candidate,
        page_hash,
        MathReviewDisposition.REJECTED,
        reviewer_task="attack-test",
    )
    decisions = tmp_path / "decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    packet_id = "attack-test-packet"
    packet_hash = _structural_packet(root, packet_id, [equation], page_hash)
    payload: dict[str, object] = {
        "schema_version": 5,
        "kind": "math-structural-review-sidecar",
        "packet_id": packet_id,
        "packet_payload_sha256": packet_hash,
        "overrides": [_structural_override(decision, packet_id, packet_hash)],
    }
    assert callable(mutation)
    mutation(payload)  # type: ignore[operator]
    sidecar = tmp_path / "attack.yaml"
    write_yaml(sidecar, payload)

    with pytest.raises(ValueError, match=message):
        import_math_review(
            root,
            decisions,
            confirm_visual_review=True,
            structural_file=sidecar,
        )
    assert not (root / "overrides" / "layout.yaml").exists()
    assert not (root / "evidence" / "math" / "reviews.jsonl").exists()


def test_structural_sidecar_rejects_cross_packet_target_and_stale_unit_binding(
    tmp_path: Path,
) -> None:
    root, equation, inline, candidate, _ = _project(tmp_path)
    build_math_review_report(root)
    page_hash = sha256_file(root / "derived" / "verification" / "page-0001.png")
    decision = _decision(
        root,
        equation,
        candidate,
        page_hash,
        MathReviewDisposition.REJECTED,
        reviewer_task="cross-packet-test",
    )
    decisions = tmp_path / "decisions.jsonl"
    decisions.write_text(decision.model_dump_json() + "\n", encoding="utf-8")
    packet_id = "cross-packet"
    packet_hash = _structural_packet(root, packet_id, [equation], page_hash)
    override = _structural_override(
        decision,
        packet_id,
        packet_hash,
        action="merge",
        target_unit_ids=[inline.unit_id],
        fields=["latex"],
        final_values={"latex": "x=y+1"},
    )
    # Recompute after replacing the action-specific values.
    override["canonical_override_sha256"] = canonical_math_structural_override_sha256(override)
    sidecar = tmp_path / "cross.yaml"
    _write_sidecar(sidecar, packet_id, packet_hash, [override])
    with pytest.raises(ValueError, match="different packet"):
        import_math_review(
            root,
            decisions,
            confirm_visual_review=True,
            structural_file=sidecar,
        )

    stale_override = _structural_override(
        decision,
        packet_id,
        packet_hash,
        source_hash="0" * 64,
    )
    stale_override["canonical_override_sha256"] = canonical_math_structural_override_sha256(
        stale_override
    )
    _write_sidecar(sidecar, packet_id, packet_hash, [stale_override])
    with pytest.raises(ValueError, match="source_hash|source hash"):
        import_math_review(
            root,
            decisions,
            confirm_visual_review=True,
            structural_file=sidecar,
        )
