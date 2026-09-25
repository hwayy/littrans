"""Regressions for PLUGIN-ISSUES.md LT-083..085 (chapters 4 and 7, round 17)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf as fitz
import pytest
from test_fidelity_source import approve
from test_source_structure_v8 import _line, _owned_text, _Page

from littrans import fidelity
from littrans.fidelity import (
    _grow_table_header,
    _regions,
    _script_digit_ids,
    build_source_review_packet,
    import_source_review,
    prepare_source,
    verify_fidelity,
)
from littrans.models import SourceUnit
from littrans.project import ProjectConfig, initialize_project_dirs, save_project
from littrans.storage import read_json, read_jsonl, sha256_file, write_json


def _texts(regions: list[dict[str, Any]], glyphs: list[dict[str, Any]]) -> list[str]:
    return [t.strip() for t in _owned_text(regions, glyphs)]


def _glyph(gid: str, text: str, x: float, *, font: str = "CMR10", size: float = 10.0, baseline: float = 28.0,
           line: str = "b0-l0", width: float = 5.0) -> dict[str, Any]:
    return {"id": gid, "text": text, "font": font, "bbox": [x, baseline - size * 0.8, x + width, baseline + size * 0.2],
            "line": line, "size": size, "baseline": baseline, "origin": [x, baseline]}


def _script_line(prefix: str, base: str, script: str, tail: str = "", *, raised: bool = True,
                 script_size: float = 8.0, gap: float = 0.0) -> list[dict[str, Any]]:
    """``prefix base^{script} tail`` in the text face; ``tail`` may carry a math-face minus."""
    glyphs: list[dict[str, Any]] = []
    x = 0.0
    for index, char in enumerate(prefix):
        glyphs.append(_glyph(f"b0-l0-p{index}", char, x))
        x += 6.0
    for index, char in enumerate(base):
        glyphs.append(_glyph(f"b0-l0-b{index}", char, x))
        x += 6.0
    x += gap
    for index, char in enumerate(script):
        glyphs.append(_glyph(f"b0-l0-s{index}", char, x, size=script_size, baseline=28.0 - 4.0 if raised else 28.0 + 1.6, width=4.0))
        x += 4.2
    for index, char in enumerate(tail):
        font = "CMSY10" if char == "−" else "CMR10"
        glyphs.append(_glyph(f"b0-l0-t{index}", char, x, font=font))
        x += 6.0
    return glyphs


# --- LT-083: script digits of a text-face number are notation ---------------------------

def test_exponent_of_a_text_face_number_joins_the_number_and_the_run_after_it() -> None:
    line = _script_line("up to ", "2", "19937", " − 1 in")
    scripts = _script_digit_ids({"b0-l0": line})
    assert scripts == {f"b0-l0-s{i}" for i in range(5)}
    assert _texts(_regions(_Page(), line, []), line) == ["219937 − 1"]
    # A power with no mathematical glyph at all is a run of its own.
    line = _script_line("from ", "10", "6", " samples")
    assert _texts(_regions(_Page(), line, []), line) == ["106"]


def test_a_superscript_after_a_letter_or_punctuation_is_not_an_exponent() -> None:
    call = _script_line("word.", "", "1", " Next")
    assert _script_digit_ids({"b0-l0": call}) == set()
    assert _texts(_regions(_Page(), call, []), call) == []
    letter = _script_line("see x", "", "2", " and")
    assert _script_digit_ids({"b0-l0": letter}) == set()


def test_script_digits_need_a_smaller_size_another_baseline_and_no_gap() -> None:
    same_size = _script_line("", "2", "19", script_size=10.0)
    assert _script_digit_ids({"b0-l0": same_size}) == set()
    same_baseline = [dict(g, baseline=28.0, origin=[g["origin"][0], 28.0]) for g in _script_line("", "2", "19")]
    assert _script_digit_ids({"b0-l0": same_baseline}) == set()
    spaced = _script_line("", "2", "19", gap=4.0)
    assert _script_digit_ids({"b0-l0": spaced}) == set()
    lowered = _script_line("", "2", "19", raised=False)
    assert _script_digit_ids({"b0-l0": lowered}) == {"b0-l0-s0", "b0-l0-s1"}


# --- LT-084: a detector table box takes its header rows above it -------------------------

def _table(header_block: str = "b3", header_x: float = 130.0, caption: bool = False) -> tuple[list[dict[str, Any]], list[float]]:
    header_text = "Table 4.1 sites" if caption else "Class Spin Count"
    header = _line(header_text, "CMR10", line=f"{header_block}-l0", y=460.0, x=header_x)
    rows = [_line("1 up 2", "CMR10", line=f"b3-l{i + 1}", y=480.0 + 14 * i, x=140.0) for i in range(3)]
    glyphs = header + [g for row in rows for g in row]
    box = [125.0, 477.0, 260.0, 530.0]
    return glyphs, box


class _RuledPage(_Page):
    def __init__(self, rules: list[tuple[float, float, float, float]]) -> None:
        self.rules = rules

    def get_drawings(self) -> list[Any]:
        return [{"rect": fitz.Rect(*rule)} for rule in self.rules]


def test_header_row_in_the_tables_block_grows_the_box() -> None:
    glyphs, box = _table()
    grown = _grow_table_header(box, glyphs, margin=58.7, rules=[])
    assert grown[1] < 460.0 and grown[0] == box[0] and grown[2:] == box[2:]
    # Two header rows are taken one after the other.
    second = _line("Site data", "CMR10", line="b3-l9", y=446.0, x=140.0)
    grown = _grow_table_header(box, glyphs + second, margin=58.7, rules=[])
    assert grown[1] < 446.0


def test_caption_wide_and_margin_rows_are_not_header_rows() -> None:
    glyphs, box = _table(caption=True)
    assert _grow_table_header(box, glyphs, margin=58.7, rules=[]) == box
    glyphs, box = _table(header_x=100.0)
    assert _grow_table_header(box, glyphs, margin=58.7, rules=[]) == box
    # A row in another block at the prose margin is a paragraph, unless a rule of the
    # table's width separates it from the rows below.
    glyphs, box = _table(header_block="b2", header_x=58.7)
    box = [55.0, 477.0, 260.0, 530.0]
    assert _grow_table_header(box, glyphs, margin=58.7, rules=[]) == box
    assert _grow_table_header(box, glyphs, margin=58.7, rules=[[56.0, 472.0, 259.0, 472.4]])[1] < 460.0


def test_detector_table_region_covers_the_header_row_above_its_box() -> None:
    glyphs, box = _table()
    items = [{"label": "table", "bbox": [v * 2 for v in box], "score": 0.96}]
    table = next(r for r in _regions(_RuledPage([(126.0, 478.0, 259.0, 478.4)]), glyphs, items) if r["kind"] == "table")
    assert table["bbox"][1] < 460.0
    header_ids = {g["id"] for g in glyphs if g["line"] == "b3-l0"}
    assert all(fidelity._inside(g, table["bbox"]) for g in glyphs if g["id"] in header_ids)


# --- LT-085: receipts of pages a correction reaches only through its new edge -----------

@pytest.fixture
def two_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {"status": "unavailable", "reason": "isolated test", "pages": {}})
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 80), "Solution. The first step is easy.")
        page = doc.new_page()
        page.insert_text((60, 80), "The second step follows.")
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="t", title="t", source_path="source/book.pdf", source_sha256=sha256_file(pdf), source_pages=2, profile="technical-book"))
    return tmp_path


def _reparent_page_two(root: Path) -> dict[str, Any]:
    units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    first = next(u for u in units if u.page == 1 and u.translatable)
    packet = build_source_review_packet(root, "2")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "corrector"
    review["pages"][0]["override"] = {"regions": [], "units": [
        {"unit_id": u.unit_id, "kind": u.kind.value, "bbox": list(u.bbox), "source_markdown": u.source_text,
         "parent_id": first.unit_id if u.translatable else u.parent_id, "render_policy": u.render_policy.value,
         "translatable": u.translatable}
        for u in units if u.page == 2]}
    write_json(root / "override.json", review)
    return import_source_review(root, root / "override.json", True)


def test_override_that_parents_a_page_to_its_predecessor_invalidates_the_predecessor(two_pages: Path) -> None:
    prepare_source(two_pages, "1-2", allow_missing_layout=True)
    from littrans.evidence import page_evidence_units
    units = read_jsonl(two_pages / "derived/units.jsonl", SourceUnit)
    assert {u.page for u in page_evidence_units(1, units)} == {1}
    assert approve(two_pages, "1-2")["approved_pages"] == [1, 2]
    assert verify_fidelity(two_pages, "1-2")["passed"]
    result = _reparent_page_two(two_pages)
    assert result["changed_pages"] == [2] and result["invalidated_pages"] == [1] and result["requires_new_packet"]
    assert not (two_pages / "evidence/pages/fidelity-p0001.review.json").is_file()
    units = read_jsonl(two_pages / "derived/units.jsonl", SourceUnit)
    assert {u.page for u in page_evidence_units(1, units)} == {1, 2}
    # Nothing is left for a later full-range verify to discover.
    assert not any("requires" in str(f) for page in verify_fidelity(two_pages, "1-2").get("pages", []) for f in page.get("failures", []))


def test_re_preparation_that_adds_a_cross_page_edge_invalidates_the_page_it_reaches(two_pages: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_source(two_pages, "1-2", allow_missing_layout=True)
    assert approve(two_pages, "1-2")["approved_pages"] == [1, 2]
    units = read_jsonl(two_pages / "derived/units.jsonl", SourceUnit)
    first = next(u for u in units if u.page == 1 and u.translatable)
    original = fidelity._page_prepare

    def linked(root: Path, doc: Any, number: int, *args: Any, **kwargs: Any) -> Any:
        page_units, page_assets, ledger = original(root, doc, number, *args, **kwargs)
        if number == 2:
            page_units = [u.model_copy(update={"parent_id": first.unit_id}) if u.translatable else u for u in page_units]
        return page_units, page_assets, ledger

    monkeypatch.setattr(fidelity, "_page_prepare", linked)
    result = prepare_source(two_pages, "2", replace=True, allow_missing_layout=True)
    # Page 2 (re-prepared, LT-089) and page 1 (the dependant it now reaches) both lost their receipts.
    assert result["invalidated_pages"] == [1, 2] and result["retained_receipt_pages"] == []
    assert not (two_pages / "evidence/pages/fidelity-p0001.review.json").is_file()
