"""Correctness boundaries of template reuse, geometry indexing and packet parsing."""
import hashlib
import math
import os
import random
import shutil
from collections import Counter
from pathlib import Path

import pymupdf as fitz
import pytest
from synthetic_fixtures import copy_workflow_template, isolated_template
from test_workflow_v6 import project  # noqa: F401

from littrans import fidelity, glyph_export, layout_detector
from littrans.storage import load_project, read_json


def _scan(glyphs, x, y):
    return [g for g in glyphs if abs(g["origin"][0] - x) < .03
            and abs(g["origin"][1] - y) < .03]


def test_origin_index_matches_scan_at_boundaries_and_duplicate_origins():
    rng = random.Random(726)
    points = [(rng.uniform(-20, 20), rng.uniform(-20, 20)) for _ in range(300)]
    for anchor in (-.12, -.06, 0., .06, .12, 1e9):
        for offset in (0., .03, -.03, math.nextafter(.03, 0.), math.nextafter(.03, 1.)):
            points.extend([(anchor + offset, anchor), (anchor, anchor + offset)])
    points.extend([(0., 0.), (0., 0.)])
    glyphs = [{"id": str(i), "origin": point} for i, point in enumerate(points)]
    index = glyph_export._GlyphOriginIndex(glyphs)
    for x, y in [*points, *[(x + .029, y - .029) for x, y in points]]:
        assert index.matches(x, y) == _scan(glyphs, x, y)


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), -float("inf"), 1e20])
def test_origin_index_falls_back_for_unusual_coordinates(coordinate):
    glyphs = [{"id": "unusual", "origin": (coordinate, 0.)},
              {"id": "ordinary", "origin": (0., 0.)}]
    index = glyph_export._GlyphOriginIndex(glyphs)
    for x in (0., coordinate):
        assert index.matches(x, 0.) == _scan(glyphs, x, 0.)


@pytest.mark.parametrize("rotation,crop", [(0, False), (90, False), (0, True)])
def test_indexed_ink_matches_original_scan(monkeypatch, rotation, crop):
    with fitz.open() as document:
        page = document.new_page(width=300, height=300)
        page.insert_text((60, 90), "x+y", fontsize=19, rotate=rotation)
        page.insert_text((60, 90), "/", fontsize=19, rotate=rotation)
        if crop:
            page.set_cropbox(fitz.Rect(10, 10, 290, 290))
        chars = [c for b in page.get_text("rawdict")["blocks"] for line in b["lines"]
                 for span in line["spans"] for c in span["chars"]]
        glyphs = [{"id": str(i), "origin": c["origin"]} for i, c in enumerate(chars)]
        actual = glyph_export.glyph_ink_boxes(page, glyphs)
        assert actual
        monkeypatch.setattr(glyph_export._GlyphOriginIndex, "matches",
                            lambda self, x, y: _scan(self.glyphs, x, y))
        assert actual == glyph_export.glyph_ink_boxes(page, glyphs)


def test_template_copies_preserve_receipts_and_are_independent(tmp_path, workflow_v6_template):
    def digest_tree(root):
        return {p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob("*") if p.is_file()}
    before = digest_tree(workflow_v6_template)
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = copy_workflow_template(workflow_v6_template, first_dir)
    second = copy_workflow_template(workflow_v6_template, second_dir)
    assert load_project(first).source(first) == first_dir / "oracle.pdf"
    assert fidelity.verify_fidelity(first)["passed"]
    assert fidelity.verify_fidelity(second)["passed"]
    (first / "derived/units.jsonl").write_text("broken", encoding="utf-8")
    shutil.rmtree(first_dir)
    assert fidelity.verify_fidelity(second)["passed"]
    assert digest_tree(workflow_v6_template) == before


@pytest.mark.parametrize("builder_name", ["legacy", "v6"])
def test_session_builders_isolate_detector_and_restore_environment(tmp_path, monkeypatch, builder_name):
    from test_workflow import _build_prepared_project
    from test_workflow_v6 import _build_workflow_project

    def forbidden(*args, **kwargs):
        pytest.fail("Synthetic session template reached the real layout runtime")
    monkeypatch.setenv("CURSOR_TRACE_ID", "outside-host")
    monkeypatch.setenv("LITTRANS_CACHE_DIR", "outside-cache")
    monkeypatch.setattr(fidelity, "detect_layout", forbidden)
    monkeypatch.setattr(layout_detector, "model_weight_hashes", forbidden)
    monkeypatch.setattr(layout_detector, "_runtime_identity", forbidden)
    builder = _build_prepared_project if builder_name == "legacy" else _build_workflow_project
    with isolated_template(tmp_path / "cache"):
        assert "CURSOR_TRACE_ID" not in os.environ
        assert os.environ["CODEX_CI"] == "1"
        root = builder(tmp_path)
        assert fidelity.verify_fidelity(root)["passed"]
    assert fidelity.detect_layout is forbidden
    assert os.environ["CURSOR_TRACE_ID"] == "outside-host"
    assert os.environ["LITTRANS_CACHE_DIR"] == "outside-cache"
    with pytest.raises(RuntimeError), isolated_template(tmp_path / "other-cache"):
        raise RuntimeError("interrupted construction")
    assert fidelity.detect_layout is forbidden
    assert os.environ["LITTRANS_CACHE_DIR"] == "outside-cache"


def _packet_paths(root):
    receipt = read_json(root / "evidence/pages/fidelity-p0001.review.json")
    packet_path = root / "packets" / receipt["packet_id"] / "packet.json"
    packet = read_json(packet_path)
    report = packet_path.parent / "coverage.html"
    image = root / next(iter(packet["visual_report"]["files"]))
    return receipt, packet_path, report, image


def test_packet_parsing_is_local_but_current_bytes_and_dependencies_are_rechecked(project, monkeypatch):  # noqa: F811
    _, packet_path, report, image = _packet_paths(project)
    packet_text = packet_path.read_text(encoding="utf-8")
    loads, reads, hashes, identities = Counter(), Counter(), Counter(), Counter()
    original_loads, original_read = fidelity.json.loads, Path.read_bytes
    original_hash, original_identity = fidelity.sha256_file, fidelity._source_packet_identity

    def counted_loads(data, *args, **kwargs):
        if data == packet_text:
            loads["packet"] += 1
        return original_loads(data, *args, **kwargs)

    def counted_read(path):
        reads[path] += 1
        return original_read(path)

    def counted_hash(path):
        hashes[path] += 1
        return original_hash(path)

    def counted_identity(payload):
        identities["packet"] += 1
        return original_identity(payload)

    monkeypatch.setattr(fidelity.json, "loads", counted_loads)
    monkeypatch.setattr(Path, "read_bytes", counted_read)
    monkeypatch.setattr(fidelity, "sha256_file", counted_hash)
    monkeypatch.setattr(fidelity, "_source_packet_identity", counted_identity)
    for invocation in (1, 2):
        assert fidelity.verify_fidelity(project)["passed"]
        assert loads["packet"] == identities["packet"] == invocation
        assert reads[packet_path] == invocation * 2
        assert hashes[report] >= invocation * 2
        assert hashes[image] >= invocation * 2


@pytest.mark.parametrize("target", ["packet", "report", "image"])
@pytest.mark.parametrize("mutation", ["replace", "delete"])
@pytest.mark.parametrize("timing", ["within", "between"])
def test_packet_cache_detects_changed_dependencies(project, monkeypatch, target, mutation, timing):  # noqa: F811
    _, packet_path, report, image = _packet_paths(project)
    path = {"packet": packet_path, "report": report, "image": image}[target]

    def damage():
        if mutation == "delete":
            path.unlink()
        else:
            content = bytearray(path.read_bytes())
            content[-1] ^= 1  # Same length, and preserve mtime to defeat metadata-only caches.
            stat = path.stat()
            replacement = path.with_suffix(path.suffix + ".replacement")
            replacement.write_bytes(content)
            replacement.replace(path)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    if timing == "within":
        original = fidelity._verify_source_receipt
        calls = 0

        def verify_then_damage(*args, **kwargs):
            nonlocal calls
            original(*args, **kwargs)
            calls += 1
            if calls == 1:
                damage()
        monkeypatch.setattr(fidelity, "_verify_source_receipt", verify_then_damage)
    else:
        assert fidelity.verify_fidelity(project)["passed"]
        damage()
    result = fidelity.verify_fidelity(project)
    assert not result["passed"]
    if timing == "within":
        assert result["verified_pages"] == [1]
        assert any(error["page"] == 2 for error in result["errors"])


def test_packet_cache_does_not_accept_a_different_expected_hash(project):  # noqa: F811
    receipt, _, _, _ = _packet_paths(project)
    cache = {}
    fidelity._load_source_packet(project, receipt["packet_id"], receipt["packet_sha256"], parsed_packets=cache)
    with pytest.raises(ValueError, match="hash mismatch"):
        fidelity._load_source_packet(project, receipt["packet_id"], "0" * 64, parsed_packets=cache)
    assert len(cache) == 1
