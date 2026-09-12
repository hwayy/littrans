from pathlib import Path

import pymupdf as fitz
import pytest
from test_fidelity_source import approve
from test_workflow_v6 import project as workflow_project

from littrans import fidelity
from littrans.models import SourceUnit, UnitKind
from littrans.rendering import _markdown_footnote_calls, render_project
from littrans.source_render import render_source_review
from littrans.source_structure import plan_structure
from littrans.storage import read_json, read_jsonl, write_json, write_jsonl

project = workflow_project


@pytest.mark.parametrize(
    "bad_id", ['x"><img src=x onerror=alert(1)>', "../escape", "", "has space"]
)
def test_override_rejects_unsafe_unit_ids(project: Path, bad_id: str) -> None:
    packet = fidelity.build_source_review_packet(project, "1")
    page = read_json(Path(packet["packet_path"]))["pages"][0]
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "test"
    units = [
        {
            "unit_id": u["unit_id"],
            "source_markdown": u["source_markdown"] or u["source_text"],
            "bbox": u["bbox"],
            "kind": u["kind"],
        }
        for u in page["units"]
    ]
    units[0]["unit_id"] = bad_id
    review["pages"][0]["override"] = {"units": units}
    path = project / "unsafe-unit.json"
    write_json(path, review)
    before = (project / "derived/units.jsonl").read_bytes()
    with pytest.raises(ValueError, match="unit ID|unit id"):
        fidelity.import_source_review(project, path, True)
    assert (project / "derived/units.jsonl").read_bytes() == before


@pytest.mark.parametrize("math_text", [r"\(x[^1]\)", r"\[x[^1]\]", "$x[^1]$", "$$x[^1]$$"])
def test_all_math_delimiters_preserve_literal_calls(math_text: str) -> None:
    caller = SourceUnit(
        unit_id="caller",
        kind=UnitKind.PARAGRAPH,
        page=1,
        bbox=(0, 0, 1, 1),
        source_text="",
        source_hash="x",
        confidence=1,
        footnote_refs=["note"],
    )
    note = caller.model_copy(
        update={"unit_id": "note", "kind": UnitKind.FOOTNOTE, "footnote_number": "1"}
    )
    text = math_text + " Call[^1]"
    result = _markdown_footnote_calls(text, caller, {"note": note})
    assert result.startswith(math_text + " Call[^fn-")


@pytest.mark.parametrize("content", ["123 456 789", "+ - = /", ""])
def test_structure_handles_pages_without_letters(content: str) -> None:
    with fitz.open() as doc:
        p = doc.new_page()
        p.insert_text((50, 200), content, fontsize=14)
        glyphs, blocks = fidelity._native(p)
        plan = plan_structure(glyphs, blocks, [], p.rect.height)
        assert plan


@pytest.mark.parametrize("damage", ["missing", "stale", "digest"])
def test_existing_source_render_verifies_cross_page_receipt(project: Path, damage: str) -> None:
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    caller = next(u for u in units if u.page == 1 and u.kind is UnitKind.PARAGRAPH)
    note = next(u for u in units if u.page == 2 and u.kind is UnitKind.PARAGRAPH)
    caller.source_text += " Call[^1]"
    caller.source_markdown = (caller.source_markdown or caller.source_text) + " Call[^1]"
    caller.footnote_refs = [note.unit_id]
    note.kind = UnitKind.FOOTNOTE
    note.footnote_number = "1"
    write_jsonl(project / "derived/units.jsonl", units)
    approve(project, "all")
    assert render_source_review(project, "1", name="good")["source_verified"]
    path = project / "evidence/pages/fidelity-p0002.review.json"
    if damage == "missing":
        path.unlink()
    else:
        receipt = read_json(path)
        if damage == "stale":
            receipt["fingerprint"] = "old"
        else:
            receipt["receipt_sha256"] = "bad"
        write_json(path, receipt)
    result = render_source_review(project, "1", name="bad")
    assert not result["source_verified"]


def test_markdown_escapes_legacy_unsafe_anchor(project: Path) -> None:
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    unit = next(u for u in units if u.kind is UnitKind.PARAGRAPH)
    unit.unit_id = 'x"><img src=x onerror=alert(1)>'
    write_jsonl(project / "derived/units.jsonl", units)
    result = render_project(project, "all", name="old-id", allow_draft=True)
    path = next(Path(v) for v in result.values() if isinstance(v, str) and v.endswith(".zh.md"))
    text = path.read_text(encoding="utf-8")
    assert "<img src=x onerror=" not in text
    assert "&lt;img src=x onerror=" in text


@pytest.mark.parametrize("content", ["123 456 789", "+ - = /"])
def test_source_prepare_accepts_non_alphabetic_page(tmp_path: Path, content: str) -> None:
    from littrans.project import initialize_project

    source = tmp_path / "numeric.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((50, 200), content, fontsize=14)
        document.save(source)
    root = tmp_path / "project"
    initialize_project(source, root, "technical-book")
    result = fidelity.prepare_source(root, allow_missing_layout=True)
    assert result["prepared_pages"] == [1]
    assert not fidelity.verify_fidelity(root)["passed"]
