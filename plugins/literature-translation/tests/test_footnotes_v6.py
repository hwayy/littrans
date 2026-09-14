from __future__ import annotations

from pathlib import Path

import pytest
from fidelity_fixtures import review_fixture_metadata
from test_efficiency_v4 import _make_project

from littrans.evidence import dependency_closure, page_evidence_units
from littrans.fidelity import build_source_review_packet, import_source_review, verify_fidelity
from littrans.models import SourceUnit, UnitKind
from littrans.storage import read_json, read_jsonl, write_json, write_jsonl


def _linked_notes(tmp_path: Path):
    root, manifests = _make_project(tmp_path, pages=3, max_words=100)
    units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    # Create a complete synthetic caller/definition pair through the source packet.
    packet = build_source_review_packet(root, "1-2")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "synthetic-footnote-fixture-oracle"
    for decision in review["pages"]:
        unit = units[decision["page"] - 1]
        corrected = {"unit_id": unit.unit_id, "kind": "paragraph" if unit.page == 1 else "footnote",
                     "bbox": list(unit.bbox), "source_markdown": unit.source_text}
        if unit.page == 1:
            corrected["source_markdown"] += " Call[^1]"
            corrected["footnote_refs"] = [units[1].unit_id]
        else:
            corrected["footnote_number"] = "1"
        decision["override"] = {"regions": [], "units": [corrected]}
    path = root / "tmp/footnote-review.json"
    write_json(path, review)
    assert import_source_review(root, path, confirm_visual_review=True)["requires_new_packet"]
    review_fixture_metadata(root)
    units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    return root, manifests, units


def test_footnote_links_expand_audit_and_page_dependencies_both_ways(tmp_path: Path) -> None:
    root, manifests, units = _linked_notes(tmp_path)
    body, note, unrelated = units
    assert note.kind == UnitKind.FOOTNOTE and note.footnote_number == "1"
    assert body.footnote_refs == [note.unit_id]
    for batch, changed in ((manifests[0], body), (manifests[1], note)):
        assert dependency_closure(root, [batch.batch_id], [changed.unit_id]) == [body.unit_id, note.unit_id]
    for page in (body.page, note.page):
        assert [u.unit_id for u in page_evidence_units(page, units)] == [body.unit_id, note.unit_id]
    assert [u.unit_id for u in page_evidence_units(unrelated.page, units)] == [unrelated.unit_id]
    assert verify_fidelity(root)["passed"]
    # A stale footnote affects its caller's source receipt, but leaves page 3 current.
    note.source_text += " Changed."
    write_jsonl(root / "derived/units.jsonl", units)
    assert not verify_fidelity(root, "1")["passed"]
    assert not verify_fidelity(root, "2")["passed"]
    assert verify_fidelity(root, "3")["passed"]


@pytest.mark.parametrize("problem", ["missing", "not-footnote", "duplicate"])
def test_invalid_footnote_refs_cannot_receive_source_review(tmp_path: Path, problem: str) -> None:
    root, _manifests, units = _linked_notes(tmp_path)
    body, note, unrelated = units
    body.footnote_refs = {"missing": ["missing-note"], "not-footnote": [unrelated.unit_id],
                          "duplicate": [note.unit_id, note.unit_id]}[problem]
    write_jsonl(root / "derived/units.jsonl", units)
    before = (root / "derived/units.jsonl").read_bytes()
    with pytest.raises(ValueError, match="invalid or duplicated footnote"):
        build_source_review_packet(root, "1")
    assert not verify_fidelity(root, "1")["passed"]
    assert (root / "derived/units.jsonl").read_bytes() == before
