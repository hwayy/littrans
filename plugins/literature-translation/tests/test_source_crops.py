from __future__ import annotations

import copy
from pathlib import Path

import pytest
from test_workflow_v6 import project as workflow_project

from littrans.fidelity import build_source_review_packet, import_source_review, prepare_source
from littrans.fidelity_models import load_assets
from littrans.source_crops import preview_review
from littrans.storage import read_json, sha256_file, write_json

project = workflow_project


def correction(root: Path) -> tuple[dict, Path, str]:
    packet = build_source_review_packet(root, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "synthetic-crop-test"
    review["pages"] = [d for d in review["pages"] if d["page"] == 1]
    asset = next(a for a in load_assets(root).values() if a.fragments[0].page == 1)
    box = list(asset.fragments[0].bbox)
    box[2] -= 0.1
    review["pages"][0]["override"] = {"asset_crops": [
        {"asset_id": asset.id, "fragment_index": 0, "bbox": box, "reason": "Synthetic geometry oracle"}]}
    path = root / "crop-review.json"
    write_json(path, review)
    return review, path, asset.id


def test_preview_import_and_replay_preserve_exact_box_and_ownership(project: Path) -> None:
    review, path, aid = correction(project)
    before = load_assets(project)[aid]
    tracked = [project / "derived/units.jsonl", project / "derived/fidelity-assets.jsonl",
               project / "evidence/pages/fidelity-p0001.review.json"]
    hashes = {str(p): sha256_file(p) for p in tracked}
    result = preview_review(project, path, project / "output/crop-preview")
    assert result["approved_pages"] == []
    assert hashes == {str(p): sha256_file(p) for p in tracked}
    preview = read_json(Path(result["report"]))
    preview_asset = next(a for a in preview["pages"][0]["assets"] if a["id"] == aid)
    imported = import_source_review(project, path, True)
    assert imported["changed_pages"] == [1]
    assert imported["approved_pages"] == []
    after = load_assets(project)[aid]
    assert list(after.fragments[0].bbox) == review["pages"][0]["override"]["asset_crops"][0]["bbox"]
    assert after.fragments[0].glyph_ids == before.fragments[0].glyph_ids
    assert after.formula_conditions == before.formula_conditions
    assert after.fragments[0].file_sha256 == preview_asset["fragments"][0]["file_sha256"]
    prepare_source(project, "1", replace=True, allow_missing_layout=True)
    assert load_assets(project)[aid] == after


@pytest.mark.parametrize("change", ["unknown", "duplicate", "nan", "outside", "reason", "index", "binding"])
def test_invalid_crop_leaves_authority_unchanged(project: Path, change: str) -> None:
    review, path, _ = correction(project)
    entries = review["pages"][0]["override"]["asset_crops"]
    item = entries[0]
    if change == "unknown":
        item["asset_id"] = "missing"
    elif change == "duplicate":
        entries.append(copy.deepcopy(item))
    elif change == "nan":
        item["bbox"][0] = float("nan")
    elif change == "outside":
        item["bbox"][0] = -1
    elif change == "reason":
        item["reason"] = " "
    elif change == "index":
        item["fragment_index"] = True
    else:
        item["glyph_ids"] = []
    write_json(path, review)
    tracked = list((project / "derived/fidelity-pages").glob("*.json")) + [
        project / "derived/units.jsonl", project / "derived/fidelity-assets.jsonl"]
    hashes = {str(p): sha256_file(p) for p in tracked}
    with pytest.raises(ValueError):
        import_source_review(project, path, True)
    assert hashes == {str(p): sha256_file(p) for p in tracked}


def test_crop_preserves_other_fragments_and_survives_failed_measurement(project: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    import pymupdf as fitz

    from littrans import glyph_export
    from littrans.fidelity import _native
    from littrans.source_crops import apply_crops
    from littrans.storage import load_project

    asset = next(a for a in load_assets(project).values() if a.fragments[0].page == 1)
    other = asset.fragments[0].model_copy(deep=True)
    asset.fragments.append(other)
    before = other.model_dump(mode="json")
    def unavailable(*args: object) -> dict:
        raise ValueError("unsupported path measurement")
    monkeypatch.setattr(glyph_export, "glyph_ink_boxes", unavailable)
    config = load_project(project)
    with fitz.open(config.source(project)) as doc:
        glyphs, _ = _native(doc[0])
        report = apply_crops(project, doc, 1, config.source_sha256, {asset.id: asset}, [
            {"asset_id": asset.id, "fragment_index": 0, "bbox": list(asset.fragments[0].bbox), "reason": "visual oracle"}], glyphs)
    assert asset.fragments[1].model_dump(mode="json") == before
    assert report[0]["unmeasured_glyphs"]


def test_preview_handles_project_relative_pdf_and_refuses_existing_output(project: Path) -> None:
    import shutil

    import yaml

    from littrans.storage import load_project

    source = load_project(project).source(project)
    target = project / "source/copy.pdf"
    target.parent.mkdir(exist_ok=True)
    shutil.copyfile(source, target)
    config_path = project / "project.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["source_path"] = "source/copy.pdf"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    _, path, _ = correction(project)
    output = project / "output/relative-preview"
    preview_review(project, path, output)
    with pytest.raises(ValueError, match="new independent"):
        preview_review(project, path, output)
