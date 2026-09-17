"""Paragraph white space is a structure boundary (install smoke report, 0.6.0-dev.7).

A document that spaces its paragraphs instead of indenting them showed the whole body of a
page fused into one unit: the planner's gap rule never crossed PDF blocks and assembly
re-merged every flush chunk of one group. Synthetic glyph streams and a generated page
exercise the new rule in isolation; none of this is evidence about a particular document.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pymupdf as fitz
import pytest

from littrans.fidelity import _make_unit, prepare_source
from littrans.fidelity_models import FidelityAsset, FidelityFragment
from littrans.models import ProjectConfig, SourceUnit, UnitKind
from littrans.source_structure import assemble_structure, plan_structure
from littrans.storage import initialize_project_dirs, read_jsonl, save_project, sha256_file

FONT = 10.0
MARGIN = 50.0
# Baselines sit 8 below the line top; three lines 12 apart give a 12pt pitch, so paragraph
# white space starts above max(10 * 1.75, 12 * 1.45) = 17.5pt.
PITCH = 12.0
GAP = 20.0


def _line(text: str, line: str, y: float, x: float, font: str = "CMR10") -> list[dict[str, Any]]:
    return [
        {"id": f"{line}-{i}", "text": c, "font": font, "bbox": [x + i * 6.0, y, x + i * 6.0 + 5.0, y + 10.0],
         "line": line, "size": FONT, "baseline": y + 8.0, "origin": [x + i * 6.0, y + 8.0]}
        for i, c in enumerate(text)
    ]


def _page(spec: Sequence[tuple[str, str, float, float]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(block, text, y, x)`` rows become glyphs and PDF blocks in reading order."""
    glyphs: list[dict[str, Any]] = []
    blocks: dict[str, dict[str, Any]] = {}
    for index, (bid, text, y, x) in enumerate(spec):
        line = _line(text, f"{bid}-l{index}", y, x)
        glyphs += line
        block = blocks.setdefault(bid, {"id": bid, "bbox": [x, y, x, y], "lines": []})
        block["lines"].append([g["id"] for g in line])
        block["bbox"] = [min(block["bbox"][0], x), min(block["bbox"][1], y), max(block["bbox"][2], line[-1]["bbox"][2]), max(block["bbox"][3], y + 10)]
    return glyphs, list(blocks.values())


def _breaks(spec: Sequence[tuple[str, str, float, float]], display: set[str] | None = None) -> list[str]:
    glyphs, blocks = _page(spec)
    plan = plan_structure(glyphs, blocks, [], 700, display_glyph_ids=display)
    return [chunk["id"] for chunk in plan["blocks"] if chunk.get("paragraph_break")]


# Three full lines fix the pitch and the running text's right edge (46 glyphs wide).
FULL = "The first paragraph is long enough to fill a line"
OPENING: list[tuple[str, str, float, float]] = [
    ("b0", FULL, 0, MARGIN),
    ("b0", FULL, PITCH, MARGIN),
    ("b0", FULL, 2 * PITCH, MARGIN),
]


def test_white_space_above_a_closed_line_breaks_the_paragraph_across_and_inside_blocks() -> None:
    # A short last line ending in a period, then white space: the next block opens a paragraph.
    assert _breaks([*OPENING, ("b0", "and it ends here.", 3 * PITCH, MARGIN), ("b1", "A second paragraph.", 3 * PITCH + GAP, MARGIN)]) == ["b1"]
    # The same seam inside one PDF block flags the chunk the gap split off.
    assert _breaks([*OPENING, ("b0", "and it ends here.", 3 * PITCH, MARGIN), ("b0", "A second paragraph.", 3 * PITCH + GAP, MARGIN)]) == ["b0-s2"]
    # A full line closing a sentence at its very end also closes the paragraph.
    assert _breaks([*OPENING, ("b0", FULL[:-1] + ".", 3 * PITCH, MARGIN), ("b1", "A second paragraph.", 3 * PITCH + GAP, MARGIN)]) == ["b1"]
    # Ordinary line pitch never breaks, whatever the line above looks like.
    assert _breaks([*OPENING, ("b0", "and it ends here.", 3 * PITCH, MARGIN), ("b1", "A second paragraph.", 4 * PITCH, MARGIN)]) == []


def test_a_line_pushed_down_by_tall_notation_keeps_its_paragraph() -> None:
    # A full line without terminal punctuation followed by extra white space is a tall inline
    # formula's line skip, not a paragraph end: the chunk after it is not flagged.
    assert _breaks([*OPENING, ("b0", FULL, 3 * PITCH, MARGIN), ("b1", "continues the sentence.", 3 * PITCH + GAP, MARGIN)]) == []
    # A formula row (a display or a sum's limits) says nothing about paragraph ends, whether
    # its letters are single variables, a math-face integrand or an operator name.
    assert _breaks([*OPENING, ("b1", "x = y + 1", 3 * PITCH, 150), ("b2", "where y is fixed.", 3 * PITCH + GAP, MARGIN)]) == []
    glyphs, blocks = _page([*OPENING, ("b1", "E(X) := X dP.", 3 * PITCH, 150), ("b2", "provided the integral exists.", 3 * PITCH + GAP, MARGIN)])
    for g in glyphs:
        if g["line"].startswith("b1") and g["text"] in "EXdP":
            g["font"] = "CMMI10"
    plan = plan_structure(glyphs, blocks, [], 700)
    assert not any(chunk.get("paragraph_break") for chunk in plan["blocks"])
    assert _breaks([*OPENING, ("b1", "lim sup", 3 * PITCH, 150), ("b2", "where y is fixed.", 3 * PITCH + GAP, MARGIN)]) == []
    # A tombstone or label set beside a display never opens a paragraph, even after the
    # short prose line that introduced the display.
    assert _breaks([*OPENING, ("b0", "Then", 3 * PITCH, MARGIN), ("b1", "□", 3 * PITCH + GAP, 300), ("b2", "x = y", 3 * PITCH + GAP, 150)]) == []
    assert _breaks([*OPENING, ("b0", "Then", 3 * PITCH, MARGIN), ("b1", "(1.49)", 3 * PITCH + GAP, 300), ("b2", "x = y", 3 * PITCH + GAP, 150)]) == []
    # A row set right of the text column is a display even when it carries words in braces.
    assert _breaks([*OPENING, ("b1", "dt + {terms of order two}.", 3 * PITCH, 150), ("b2", "Here we used the fact.", 3 * PITCH + GAP, MARGIN)]) == []
    assert _breaks([*OPENING, ("b1", "dt + {terms of order two}.", 3 * PITCH, MARGIN + 20), ("b2", "Here we used the fact.", 3 * PITCH + GAP, MARGIN)]) == ["b2"]
    # Nor does a detector display row that also carries condition words.
    spec = [*OPENING, ("b1", "x = 0 for all t > 0.", 3 * PITCH, 150), ("b2", "where y is fixed.", 3 * PITCH + GAP, MARGIN)]
    glyphs, _ = _page(spec)
    display = {g["id"] for g in glyphs if g["line"].startswith("b1")}
    assert _breaks(spec, display=display) == []
    # A footnote call closing the line does not hide the period before it.
    # (Calls are only recognised inside the page body, below the running-head band.)
    spec = [(b, t, y + 100, x) for b, t, y, x in [*OPENING, ("b0", "and it ends here.1", 3 * PITCH, MARGIN), ("b1", "A second paragraph.", 3 * PITCH + GAP, MARGIN)]]
    glyphs, blocks = _page(spec)
    call = next(g for g in glyphs if g["text"] == "1" and g["line"] == "b0-l3")
    call["size"] = 6.0
    call["bbox"][1] -= 3
    call["origin"][1] -= 3
    call["baseline"] -= 3
    note = [("b2", "1 The note.", 600, MARGIN)]
    note_glyphs, note_blocks = _page(note)
    layout = [{"label": "footnote", "bbox": [MARGIN * 2, 1195, 400, 1225]}]
    plan = plan_structure(glyphs + note_glyphs, blocks + note_blocks, layout, 700)
    assert call["id"] in plan["markers"]
    assert [chunk["id"] for chunk in plan["blocks"] if chunk.get("paragraph_break")] == ["b1", "b2"]


def test_a_short_listing_line_closes_and_running_material_is_no_predecessor() -> None:
    listing = [*OPENING, ("b0", "The listing follows:", 3 * PITCH, MARGIN), ("b1", '<Grid Name="Root">', 3 * PITCH + GAP, MARGIN), ("b1", "</Grid>", 4 * PITCH + GAP, MARGIN),
               ("b2", "The rule keeps a label.", 4 * PITCH + 2 * GAP, MARGIN)]
    assert _breaks(listing) == ["b1", "b2"]
    # A running header is omitted and never the line a paragraph closes against: the
    # paragraph 48pt below it is not a break, and its own lines follow at the normal pitch.
    glyphs, blocks = _page([("b0", "Running head.", 0, MARGIN), ("b1", "A paragraph after the head", 48, MARGIN), *[("b2", t, y + 60, x) for _, t, y, x in OPENING]])
    plan = plan_structure(glyphs, blocks, [{"label": "header", "bbox": [MARGIN * 2, -10, 400, 30]}], 700)
    assert plan["omitted"] == {"b0": "running-header-or-footer"}
    assert not any(chunk.get("paragraph_break") for chunk in plan["blocks"])


def test_a_page_without_breaks_keeps_its_ledger_shape() -> None:
    glyphs, blocks = _page([*OPENING, ("b1", "and it ends here.", 3 * PITCH, MARGIN)])
    plan = plan_structure(glyphs, blocks, [], 700)
    assert "paragraph_break" not in plan
    assert not any("paragraph_break" in chunk for chunk in plan["blocks"])
    assert set(plan) == {"blocks", "margin", "font_size", "indent_style", "notes", "markers", "omitted", "note_top", "first_x", "display_blocks"}


def _plan(first_x: dict[str, float], breaks: Sequence[str] = (), **extra: Any) -> dict[str, Any]:
    plan = {"omitted": {}, "notes": {}, "note_top": 999, "margin": MARGIN, "font_size": FONT, "first_x": first_x, **extra}
    if breaks:
        plan["blocks"] = [{"id": bid, "bbox": [0, 0, 0, 0], "lines": [], **({"paragraph_break": True} if bid in breaks else {})} for bid in first_x]
    return plan


def _asset(aid: str, box: tuple[float, float, float, float], display: bool = True) -> FidelityAsset:
    return FidelityAsset(
        id=aid, kind="math", source_sha256="a" * 64, content_sha256="b" * 64, display=display,
        fragments=[FidelityFragment(page=1, bbox=box, width=box[2] - box[0], height=box[3] - box[1], png_path="x.png", svg_path="x.svg", pdf_path="x.pdf",
                                    file_sha256={name: "c" * 64 for name in ("x.png", "x.svg", "x.pdf")})],
    )


def test_assembly_keeps_flush_paragraphs_apart_at_a_break_and_merges_without_one() -> None:
    assets: dict[str, FidelityAsset] = {}
    units = [
        _make_unit(1, "p0001-b1", "First paragraph ends here.", [50, 10, 300, 20], assets),
        _make_unit(1, "p0001-b2", "Second paragraph after white space.", [50, 40, 300, 50], assets),
        _make_unit(1, "p0001-b3", "Its second block, one line later.", [50, 52, 300, 62], assets),
    ]
    first_x = {"b1": MARGIN, "b2": MARGIN, "b3": MARGIN}
    result = assemble_structure(units, assets, _plan(first_x, breaks=["b2"]), _make_unit)
    assert [u.unit_id for u in result] == ["p0001-b1", "p0001-b2"]
    assert [u.parent_id for u in result] == ["p0001-b1", "p0001-b2"]
    assert result[1].source_text == "Second paragraph after white space. Its second block, one line later."
    assert list(result[1].bbox) == [50, 40, 300, 62]
    # Without the flag (a hand-built plan, or a seam a tall formula opened) the flush
    # fragments still merge as before.
    for plan in (_plan(first_x), _plan(first_x, breaks=["b9"])):
        merged = assemble_structure(units, assets, plan, _make_unit)
        assert [u.unit_id for u in merged] == ["p0001-b1"]
        assert merged[0].source_text.endswith("Its second block, one line later.")


def test_a_break_ends_an_inferred_statement_but_not_a_display_child_or_its_conclusion() -> None:
    assets = {"a1": _asset("a1", (100, 70, 150, 80))}
    units = [
        _make_unit(1, "p0001-b1", "Theorem 1. Every paragraph ends:", [50, 10, 300, 20], assets),
        _make_unit(1, "p0001-b2", "{{asset:a1}}", [100, 70, 150, 80], assets),
        _make_unit(1, "p0001-b3", "(a) a clause still of the theorem.", [50, 100, 300, 110], assets),
        _make_unit(1, "p0001-b4", "Then the conclusion of the theorem holds.", [50, 130, 300, 140], assets),
        _make_unit(1, "p0001-b5", "Plain prose after white space.", [50, 160, 300, 170], assets),
        _make_unit(1, "p0001-b6", "(b) a clause of the prose, not the theorem.", [50, 190, 300, 200], assets),
    ]
    first_x = {"b1": MARGIN, "b2": 100, "b3": MARGIN, "b4": MARGIN, "b5": MARGIN, "b6": MARGIN}
    result = assemble_structure(units, assets, _plan(first_x, breaks=["b2", "b3", "b4", "b5"]), _make_unit)
    assert [u.unit_id for u in result] == [u.unit_id for u in units]
    # The display, the bracketed clause and the prose resuming after the clauses join the
    # statement even across white space; the next flush paragraph closes it and introduces
    # the clause set under it.
    assert [u.parent_id for u in result] == ["p0001-b1"] * 4 + ["p0001-b5", "p0001-b5"]
    assert result[1].kind is UnitKind.EQUATION
    assert result[3].source_text == "Then the conclusion of the theorem holds."


def test_notes_and_reference_entries_never_join_prose() -> None:
    assets: dict[str, FidelityAsset] = {}
    units = [
        _make_unit(1, "p0001-b1", "Body text that ends the page.", [50, 10, 300, 20], assets),
        _make_unit(1, "p0001-b2", "[1] Companion Report, 2024.", [50, 600, 300, 608], assets, kind="footnote"),
        _make_unit(1, "p0001-b3", "[2] Kernel Methods, 2025.", [50, 612, 300, 620], assets, kind="footnote"),
        _make_unit(1, "p0001-b4", "Smith, J. A reference.", [50, 640, 300, 648], assets, kind="bibliography"),
        _make_unit(1, "p0001-b5", "Stray prose ordered after the notes.", [50, 660, 300, 670], assets),
    ]
    first_x = {f"b{i}": MARGIN for i in range(1, 6)}
    # No plan flag: the kinds alone keep each entry apart from prose and from each other.
    result = assemble_structure(units, assets, _plan(first_x), _make_unit)
    assert [u.unit_id for u in result] == [u.unit_id for u in units]
    assert [u.kind.value for u in result] == ["paragraph", "footnote", "footnote", "bibliography", "paragraph"]
    assert [u.parent_id for u in result] == ["p0001-b1", "p0001-b2", "p0001-b3", "p0001-b4", "p0001-b4"]
    # The wrapped fragments of one recognised footnote still merge into it.
    note_units = [
        _make_unit(1, "p0001-b1", "Main text.[^1]", [50, 50, 200, 60], assets, footnote_refs=["p0001-b2"]),
        _make_unit(1, "p0001-b2", "Note begins", [68, 500, 200, 510], assets),
        _make_unit(1, "p0001-b3", "and continues.", [50, 512, 200, 522], assets),
    ]
    plan = _plan({"b1": 68, "b2": 68, "b3": 50}, notes={"b2": {"number": "1"}}, note_top=500)
    result = assemble_structure(note_units, assets, plan, _make_unit)
    assert [u.source_text for u in result] == ["Main text.[^1]", "Note begins and continues."]
    assert result[1].kind is UnitKind.FOOTNOTE


@pytest.fixture
def spaced_project(tmp_path: Path) -> Path:
    """A page whose paragraphs are set flush with white space between them."""
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/report.pdf"
    paragraphs = [
        ("The listing below is the whole of the boundary rule. It is short on purpose: a",
         "rule that needs a comment to be read is usually the wrong rule."),
        ("The rule keeps a printed label with the item it opens. Measurements of the",
         "scheduler appear in Table 1 of the companion report."),
        ("Finally, the record is only as good as the review that approved it. A page whose",
         "boundaries were never looked at stays unverified, whatever the detector reported."),
    ]
    with fitz.open() as doc:
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 96), "3. Implementation", fontname="hebo", fontsize=14)
        y = 118.0
        for first, second in paragraphs:
            page.insert_text((72, y), first, fontname="helv", fontsize=11)
            page.insert_text((72, y + 15.5), second, fontname="helv", fontsize=11)
            y += 15.5 + 23.5
        page.insert_text((72, 678), "[1] Companion Report. Journal of Synthetic Results, 2024.", fontname="helv", fontsize=9)
        page.insert_text((72, 694), "[2] Kernel Methods. Proceedings of the Same Conference, 2025.", fontname="helv", fontsize=9)
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="spaced", title="spaced", source_path="source/report.pdf",
                                         source_sha256=sha256_file(pdf), source_pages=1, profile="technical-book"))
    return tmp_path


def test_prepared_spaced_paragraphs_are_separate_units(spaced_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.fidelity as fidelity

    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {"status": "ok", "fingerprint": "spaced", "pages": {}})
    prepare_source(spaced_project)
    units = [u for u in read_jsonl(spaced_project / "derived/units.jsonl", SourceUnit) if u.render_policy.value == "include"]
    body = [u for u in units if u.kind is UnitKind.PARAGRAPH]
    assert [u.source_text[:12] for u in body] == ["The listing ", "The rule kee", "Finally, the", "[1] Companio"]
    assert all(u.parent_id == u.unit_id for u in body)
    assert all(u.bbox[3] - u.bbox[1] < 40 for u in body[:3])
    assert body[3].bbox[1] > 660
