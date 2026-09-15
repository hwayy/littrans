"""Regressions for PLUGIN-ISSUES.md LT-032..034 (chapter-1 ledger, round 5)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_fidelity_source import approve
from test_fidelity_source import project as fidelity_project  # noqa: F401
from test_plugin_issues_ch1_round4 import _override_page_one

from littrans import fidelity, layout_detector
from littrans.fidelity import (
    _cached_layout,
    build_source_review_packet,
    import_source_review,
    prepare_source,
)
from littrans.fidelity_models import load_assets
from littrans.layout_detector import layout_page_items, layout_result_path
from littrans.storage import read_json, sha256_file, write_json

project = fidelity_project

TITLE_BOX = {"label": "title", "bbox": [100, 140, 700, 200], "score": 0.9}


def _content_keyed_detect(images: list[Path], store: Path) -> dict[str, Any]:
    """What `detect_layout` publishes since 0.6.0-dev.2: content-keyed pages in a fingerprint-named file."""
    payload = {"status": "ok", "fingerprint": "content-keyed", "pages": {sha256_file(p): [TITLE_BOX] for p in images},
               "images": {str(p.resolve()): sha256_file(p) for p in images}}
    write_json(layout_result_path(store, payload["fingerprint"]), payload)
    return payload


def _path_keyed_detect(images: list[Path], store: Path) -> dict[str, Any]:
    """What earlier builds published: pages keyed by the absolute image path, file named by page set."""
    payload = {"status": "ok", "fingerprint": "path-keyed", "pages": {str(p.resolve()): [TITLE_BOX] for p in images}}
    write_json(store / "0123abcd-page-set.json", payload)
    return payload


# --- LT-032: layout evidence follows the tree, not the root -------------------------------

def test_layout_page_items_are_found_by_content_then_by_legacy_path(tmp_path: Path) -> None:
    image = tmp_path / "evidence/pages/fidelity-p0001.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    digest = sha256_file(image)
    assert layout_page_items({"pages": {digest: [TITLE_BOX]}}, image, digest) == [TITLE_BOX]
    assert layout_page_items({"pages": {str(image.resolve()): [TITLE_BOX]}}, image, digest) == [TITLE_BOX]
    # A path-keyed result whose tree has since moved is read by the page image's file name.
    moved = {"pages": {"/elsewhere/evidence/pages/fidelity-p0001.png": [TITLE_BOX], "/elsewhere/evidence/pages/fidelity-p0002.png": []}}
    assert layout_page_items(moved, image, digest) == [TITLE_BOX]
    assert layout_page_items({"pages": {digest: {}}}, image, digest) is None
    assert layout_page_items({"pages": {}}, image, digest) is None
    assert layout_page_items({"status": "unavailable"}, image, None) is None


def test_detector_fingerprint_and_keys_do_not_depend_on_the_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    python = tmp_path / "python.exe"
    python.touch()
    model = tmp_path / "model"
    model.mkdir()
    monkeypatch.setattr(layout_detector, "runtime_paths", lambda: (python, model))
    monkeypatch.setattr(layout_detector, "_runtime_identity", lambda _: {"python": "test"})

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        request = read_json(Path(command[2]))
        write_json(Path(command[3]), {"status": "ok", "fingerprint": request["fingerprint"], "pages": {name: [TITLE_BOX] for name in request["images"]}})
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(layout_detector.subprocess, "run", run)
    results = []
    for root in ("original", "clone"):
        image = tmp_path / root / "evidence/pages/fidelity-p0001.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"same page bytes")
        results.append(layout_detector.detect_layout([image], tmp_path / root / "derived/fidelity-layout"))
    digest = sha256_file(tmp_path / "original/evidence/pages/fidelity-p0001.png")
    assert results[0]["fingerprint"] == results[1]["fingerprint"]
    assert [Path(r["path"]).name for r in results] == [results[0]["fingerprint"] + ".json"] * 2
    assert set(results[0]["pages"]) == set(results[1]["pages"]) == {digest}
    assert list(results[0]["images"].values()) == [digest]


@pytest.mark.parametrize("detect", [_content_keyed_detect, _path_keyed_detect])
def test_moved_tree_replays_its_recorded_layout_result(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, detect: Any) -> None:
    """A copy of the workspace re-imports a correction with the same layout evidence and units."""
    monkeypatch.setattr(fidelity, "detect_layout", detect)
    prepare_source(project, "1")
    ledger = read_json(project / "derived/fidelity-pages/p0001.json")
    assert ledger["layout_status"] == "ok" and any(u.startswith("p0001-") for u in ledger["unit_ids"])
    clone = tmp_path / "clone"
    shutil.copytree(project, clone)
    assert _cached_layout(clone, read_json(clone / "derived/fidelity-pages/p0001.json"))["status"] == "ok"

    def preserve_all(root: Path) -> list[str]:
        packet = build_source_review_packet(root, "1")
        review = read_json(Path(packet["review_template"]))
        review["reviewer"] = "mover"
        review["pages"] = [page for page in review["pages"] if page["page"] == 1]
        review["pages"][0]["override"] = {"regions": [{"preserve_asset_id": a["id"]} for a in read_json(Path(packet["packet_path"]))["pages"][0]["assets"]]}
        write_json(root / "override.json", review)
        assert import_source_review(root, root / "override.json", True)["changed_pages"] == [1]
        rerun = read_json(root / "derived/fidelity-pages/p0001.json")
        assert rerun["layout_status"] == "ok" and rerun["layout_fingerprint"] == ledger["layout_fingerprint"]
        return list(rerun["unit_ids"])

    assert preserve_all(clone) == preserve_all(project) == ledger["unit_ids"]


def test_missing_recorded_layout_result_stops_the_import_and_is_reported_by_replace(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fidelity, "detect_layout", _content_keyed_detect)
    prepare_source(project, "1")
    _override_page_one(project, {"id": "kept-rule", "kind": "mixed-region", "bbox": [95, 95, 155, 105], "grouping_pending": False})
    for path in (project / "derived/fidelity-layout").glob("*.json"):
        path.unlink()
    cached = _cached_layout(project, read_json(project / "derived/fidelity-pages/p0001.json"))
    assert cached["status"] == "unavailable" and "content-keyed" in cached["reason"] and "--replace" not in cached["reason"]
    # An override import never re-cuts the reviewed page by the fallback rules.
    with pytest.raises(ValueError, match=r"page 1: the layout result the ledger records .*missing from derived/fidelity-layout/.*source prepare --pages 1 --replace"):
        _override_page_one(project, {"preserve_asset_id": "kept-rule"})
    assert "kept-rule" in load_assets(project)
    # A rerun on the same runtime re-detects the recorded result itself (same fingerprint);
    # a rerun on another runtime replays the override on that detection and says so.
    result = prepare_source(project, "1", replace=True)
    assert result["replayed_override_pages"] == [1] and result["redetected_override_pages"] == []
    assert read_json(project / "derived/fidelity-pages/p0001.json")["layout_fingerprint"] == "content-keyed"

    def upgraded_detect(images: list[Path], store: Path) -> dict[str, Any]:
        payload = {"status": "ok", "fingerprint": "content-keyed-v2", "pages": {sha256_file(p): [TITLE_BOX] for p in images}}
        write_json(layout_result_path(store, payload["fingerprint"]), payload)
        return payload

    monkeypatch.setattr(fidelity, "detect_layout", upgraded_detect)
    for path in (project / "derived/fidelity-layout").glob("*.json"):
        path.unlink()
    result = prepare_source(project, "1", replace=True)
    assert result["replayed_override_pages"] == [1] and result["redetected_override_pages"] == [1]
    assert "kept-rule" in load_assets(project)
    assert read_json(project / "derived/fidelity-pages/p0001.json")["layout_fingerprint"] == "content-keyed-v2"


# --- LT-033/034: a preserved math asset is declared like any other, under its own name ----

def test_preserved_math_asset_is_declared_automatically_and_keeps_its_identity(project: Path) -> None:
    prepare_source(project, "1", allow_missing_layout=True)
    _override_page_one(project, {"id": "words", "kind": "math", "bbox": [58, 68, 90, 86]})
    declared = load_assets(project)["words"]
    assert [c.source_text for c in declared.formula_conditions] == ["Let x"]
    # The ledger scenario: a math crop that arrived without a declaration is preserved as is.
    _override_page_one(project, {"id": "silent", "kind": "math", "bbox": [58, 68, 90, 86], "formula_conditions": []})
    silent = load_assets(project)["silent"]
    assert not silent.formula_conditions and approve(project, "1", "formula_conditions_checked")["rejected_pages"] == {1: ["undeclared-formula-language: silent"]}
    _override_page_one(project, {"preserve_asset_id": "silent"})
    preserved = load_assets(project)["silent"]
    assert [c.model_dump(mode="json") for c in preserved.formula_conditions] == [c.model_dump(mode="json") for c in declared.formula_conditions]
    assert preserved.provenance == [*silent.provenance, "auto-formula-conditions"]
    # Identity is frozen at creation: the id and crop directory stay, only content_sha256 moves (LT-034).
    assert preserved.id == "silent" and preserved.fragments[0].png_path == silent.fragments[0].png_path
    assert preserved.content_sha256 != silent.content_sha256 and preserved.content_sha256 == fidelity._asset_content_identity(preserved)
    assert approve(project, "1", "formula_conditions_checked")["approved_pages"] == [1]
    # A preserved asset that already carries a declaration keeps it; an explicit list is the reviewer's.
    _override_page_one(project, {"preserve_asset_id": "silent"})
    assert load_assets(project)["silent"].formula_conditions == preserved.formula_conditions
    _override_page_one(project, {"preserve_asset_id": "silent", "formula_conditions": []})
    assert not load_assets(project)["silent"].formula_conditions


# --- LT-035: a rerun never overwrites the result a ledger records --------------------------

def test_whole_chapter_rerun_on_a_new_runtime_keeps_recorded_results_and_override_receipts(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real store: one file per fingerprint, so override pages replay on the old result."""
    python = tmp_path / "python.exe"
    python.touch()
    model = tmp_path / "model"
    model.mkdir()
    identity = {"python": "runtime-a"}
    monkeypatch.setattr(layout_detector, "runtime_paths", lambda: (python, model))
    monkeypatch.setattr(layout_detector, "_runtime_identity", lambda _: dict(identity))

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        request = read_json(Path(command[2]))
        write_json(Path(command[3]), {"status": "ok", "fingerprint": request["fingerprint"], "pages": {name: [TITLE_BOX] for name in request["images"]}})
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(layout_detector.subprocess, "run", run)
    monkeypatch.setattr(fidelity, "detect_layout", layout_detector.detect_layout)
    prepare_source(project)
    store = project / "derived/fidelity-layout"
    first = read_json(project / "derived/fidelity-pages/p0001.json")["layout_fingerprint"]
    assert (store / f"{first}.json").is_file()
    _override_page_one(project, {"id": "kept-rule", "kind": "mixed-region", "bbox": [95, 95, 155, 105], "grouping_pending": False})
    assert approve(project, "1,2")["approved_pages"] == [1, 2]
    identity["python"] = "runtime-b"
    result = prepare_source(project, replace=True)
    ledgers = {p: read_json(project / f"derived/fidelity-pages/p{p:04d}.json") for p in (1, 2)}
    # The recorded result survives the rerun beside the new one; the override page replays on it.
    assert (store / f"{first}.json").is_file() and ledgers[1]["layout_fingerprint"] == first
    assert ledgers[2]["layout_fingerprint"] != first and (store / f"{ledgers[2]['layout_fingerprint']}.json").is_file()
    assert result["replayed_override_pages"] == [1] and result["redetected_override_pages"] == []
    assert result["retained_receipt_pages"] == [1] and 2 not in result["retained_receipt_pages"]
    assert "kept-rule" in load_assets(project)
