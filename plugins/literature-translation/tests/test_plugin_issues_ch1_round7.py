"""Regressions for PLUGIN-ISSUES.md LT-036 (chapter-1 ledger, round 7).

A printed list label ("1.11.", "(b)") is a structure boundary, and the lines aligned with
its text column are its continuation, whatever the page's dominant margin is. Synthetic
glyph streams reproduce the exercise-page geometry: labels hang left of a text column that
most lines of the page start at, so the mode-of-line-starts margin is that column.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from littrans.fidelity import _make_unit, _rejoin_line_breaks
from littrans.source_structure import (
    LIST_LABEL_START,
    assemble_structure,
    list_label,
    plan_structure,
)

FONT = 10.0


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


# Exercise geometry: labels at 79.1 / clause labels at 104.6, text columns 109.1 / 128.6.
EXERCISES: list[tuple[str, str, float, float]] = [
    ("b0", "In the following, i.i.d. stands for", 0, 58.7),
    ("b1", "1.1. Let X be a variable with", 20, 79.1),
    ("b1", "Bernoulli distribution p.", 32, 109.1),
    ("b1", "1.2. Let S be the number of", 44, 79.1),
    ("b1", "tosses for a fair coin.", 56, 109.1),
    ("b1", "Its distribution is given by", 68, 109.1),
    ("b2", "1.5. Suppose X and Y are two", 92, 79.1),
    ("b2", "variables.", 104, 109.1),
    ("b2", "(a) Prove that Z is Poisson.", 116, 104.6),
    ("b2", "(b) Prove that the condi-", 128, 104.6),
    ("b3", "tioning on X being fixed is", 140, 128.6),
    ("b3", "binomial with parameter n", 152, 128.6),
    ("b3", "or with the other one.", 164, 128.6),
    ("b4", "A paragraph back at the margin.", 190, 58.7),
]


def _chunks(plan: dict[str, Any]) -> dict[str, list[str]]:
    gm = {g["id"]: g for g in plan["_glyphs"]}
    return {b["id"]: ["".join(gm[gid]["text"] for gid in line) for line in b["lines"]] for b in plan["blocks"]}


def _plan(spec: Sequence[tuple[str, str, float, float]], layout: list[dict[str, Any]] | None = None, display: set[str] | None = None) -> dict[str, Any]:
    glyphs, blocks = _page(spec)
    plan: dict[str, Any] = plan_structure(glyphs, blocks, layout or [], 700, display_glyph_ids=display)
    plan["_glyphs"] = glyphs
    return plan


def test_list_label_reads_the_hanging_number_and_its_text_column() -> None:
    line = _line("1.11. Let R be", "b0-l0", 0, 73.6)
    assert list_label(line, FONT) == ("1.11.", 73.6 + 6 * 6.0)
    assert list_label(_line("(b) Prove", "b0-l0", 0, 104.0), FONT) == ("(b)", 104.0 + 4 * 6.0)
    assert list_label(_line("2)  x", "b0-l0", 0, 50), FONT) == ("2)", 50 + 4 * 6.0)
    # A bare number wrapped to the margin, a section reference without closing punctuation,
    # a four-digit year and a label set in a mathematical face are not list labels.
    assert list_label(_line("1.32.", "b0-l0", 0, 58.7), FONT) is None
    assert list_label(_line("1.1 we saw", "b0-l0", 0, 58.7), FONT) is None
    assert list_label(_line("2019. The", "b0-l0", 0, 58.7), FONT) is None
    assert list_label(_line("(1) x = y", "b0-l0", 0, 80, font="CMMI10"), FONT) is None


def test_labels_cut_chunks_and_continuations_survive_a_list_column_margin() -> None:
    plan = _plan(EXERCISES)
    # The page's most common line start is the exercise text column, not the text margin.
    assert plan["margin"] == 109.1
    chunks = _chunks(plan)
    assert list(chunks) == ["b0", "b1", "b1-s2", "b2", "b2-s2", "b2-s3", "b3", "b4"]
    assert chunks["b1"][0][:4] == "1.1." and len(chunks["b1"]) == 2
    assert chunks["b1-s2"][0][:4] == "1.2." and len(chunks["b1-s2"]) == 3
    assert len(chunks["b2"]) == 2 and chunks["b2-s2"] == ["(a) Prove that Z is Poisson."]
    assert chunks["b2-s3"] == ["(b) Prove that the condi-"]
    # The nested clause's continuation lines stay one chunk although they sit inside the
    # drifted indent window (109.1 + 0.8em .. 109.1 + 2.8em).
    assert len(chunks["b3"]) == 3
    assert plan["list_items"] == {
        "b1": {"label": "1.1.", "body_x": 109.1},
        "b1-s2": {"label": "1.2.", "body_x": 109.1},
        "b2": {"label": "1.5.", "body_x": 109.1},
        "b2-s2": {"label": "(a)", "body_x": 128.6},
        "b2-s3": {"label": "(b)", "body_x": 128.6},
        "b3": {"continues": "b2-s3"},
    }


def test_prose_resuming_after_a_nested_list_starts_its_own_chunk() -> None:
    plan = _plan([
        ("b0", "1.6. Prove the following:", 0, 79.1),
        ("b0", "(a) the first statement,", 12, 104.6),
        ("b0", "and its second line;", 24, 128.6),
        ("b0", "(b) the second statement.", 36, 104.6),
        ("b0", "Then conclude the exercise.", 48, 109.1),
        ("b0", "1.7. Let X be Gaussian.", 60, 79.1),
    ])
    chunks = _chunks(plan)
    assert [len(c) for c in chunks.values()] == [1, 2, 1, 1, 1]
    assert plan["list_items"]["b0-s4"] == {"continues": "b0"}
    assert plan["list_items"]["b0-s5"] == {"label": "1.7.", "body_x": 109.1}


def test_numbers_opening_wrapped_prose_headings_and_display_lines_are_not_labels() -> None:
    # A sentence wrapping onto "2.3. The result" at the text column of an open item, a
    # number at the margin under an indented first line, and a number closing a line.
    plan = _plan([
        ("b0", "1.4. See the discussion of Section", 0, 79.1),
        ("b0", "2.3. The result also holds.", 12, 109.1),
        ("b1", "An indented paragraph on Section", 40, 74.7),
        ("b1", "2.3. The result also holds for", 52, 58.7),
        ("b1", "1.32.", 64, 58.7),
    ])
    chunks = _chunks(plan)
    assert [len(c) for c in chunks.values()] == [2, 3]
    assert plan["list_items"] == {"b0": {"label": "1.4.", "body_x": 109.1}}
    # Section headings inside a title box and display lines are never labels.
    glyphs, blocks = _page([("b0", "1.2. Probability Space", 0, 58.7), ("b1", "(1) x = y", 30, 100)])
    title = {"label": "paragraph_title", "bbox": [100, -10, 400, 30], "score": 0.9}
    plan = plan_structure(glyphs, blocks, [title], 700, display_glyph_ids={g["id"] for g in glyphs if g["line"].startswith("b1")})
    assert "list_items" not in plan


def test_unit_labels_open_groups_clauses_join_and_continuations_merge() -> None:
    assets: dict[str, Any] = {}
    texts = {
        "b0": "In the following, i.i.d. stands for independent.",
        "b1": "1.1. Let X be a variable with Bernoulli distribution p.",
        "b1-s2": "1.2. Let S be the number of tosses for a fair coin.",
        "b2": "1.5. Suppose X and Y are two variables.",
        "b2-s2": "(a) Prove that Z is Poisson.",
        "b2-s3": "(b) Prove that the well-",
        "b3": "known condi-",
        "b3-s2": "tioning on X being fixed is binomial.",
    }
    units = [_make_unit(1, f"p0001-{bid}", text, [50, 20 * i, 300, 20 * i + 10], assets) for i, (bid, text) in enumerate(texts.items())]
    plan = {
        "omitted": {}, "notes": {}, "note_top": 999, "margin": 109.1, "font_size": FONT,
        "first_x": {"b0": 58.7, "b1": 79.1, "b1-s2": 79.1, "b2": 79.1, "b2-s2": 104.6, "b2-s3": 104.6, "b3": 128.6, "b3-s2": 128.6},
        "list_items": {"b1": {"label": "1.1.", "body_x": 109.1}, "b1-s2": {"label": "1.2.", "body_x": 109.1},
                       "b2": {"label": "1.5.", "body_x": 109.1}, "b2-s2": {"label": "(a)", "body_x": 128.6},
                       "b2-s3": {"label": "(b)", "body_x": 128.6}, "b3": {"continues": "b2-s3"}, "b3-s2": {"continues": "b2-s3"}},
    }
    evidence = (Counter({"well-known": 3}), Counter({"conditioning": 5}))
    result = assemble_structure(units, assets, plan, _make_unit, rejoin=lambda text: _rejoin_line_breaks(text, evidence))
    by_id = {u.unit_id: u for u in result}
    assert list(by_id) == ["p0001-b0", "p0001-b1", "p0001-b1-s2", "p0001-b2", "p0001-b2-s2", "p0001-b2-s3"]
    # Each numbered exercise is its own parent group; its clauses join it.
    assert [u.parent_id for u in result] == ["p0001-b0", "p0001-b1", "p0001-b1-s2", "p0001-b2", "p0001-b2", "p0001-b2"]
    # The continuation chunks merged into the clause, and the seams obeyed the document's
    # hyphenation evidence like a line end inside a block.
    assert by_id["p0001-b2-s3"].source_text == "(b) Prove that the well-known conditioning on X being fixed is binomial."
    assert by_id["p0001-b2-s3"].bbox[3] == 150


def test_numbered_items_inside_a_statement_stay_with_it() -> None:
    assets: dict[str, Any] = {}
    texts = ["Theorem 1.2. The following are equivalent:", "1. X is measurable.", "2. Y is measurable.", "Proof. Obvious.", "Exercises", "1.1. Compute EX."]
    units = [_make_unit(1, f"p0001-b{i}", text, [50, 20 * i, 300, 20 * i + 10], assets, kind="heading" if text == "Exercises" else "paragraph") for i, text in enumerate(texts)]
    plan = {"omitted": {}, "notes": {}, "note_top": 999, "margin": 50, "font_size": FONT, "first_x": {f"b{i}": 68 for i in range(6)}}
    result = assemble_structure(units, assets, plan, _make_unit)
    assert [u.parent_id for u in result] == ["p0001-b0"] * 3 + ["p0001-b3", "p0001-b4", "p0001-b5"]
    assert len(result) == 6


def test_unit_label_pattern_requires_a_space_after_a_number() -> None:
    assert LIST_LABEL_START.match("1.11. Let R be")
    assert LIST_LABEL_START.match("**1.1.** Let")
    assert LIST_LABEL_START.match("(iv) The fourth")
    assert LIST_LABEL_START.match("1.3. {{asset:a-p0047-7377c59ceee6}}")
    assert not LIST_LABEL_START.match("1.5{{asset:a-p0047-x}} is a fraction")
    assert not LIST_LABEL_START.match("1.1 Random variables")
    assert not re.match(LIST_LABEL_START, "(1.6)")
