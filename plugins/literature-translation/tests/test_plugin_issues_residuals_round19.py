"""Regression tests for the residual PLUGIN-ISSUES forms of round 19 (0.6.1-dev.8).

LT-060: a word space after an asset that ends in a script (``C^∞ in its domain``).
LT-067: a sentence-ending ``!`` after an inline formula, told from a factorial by what follows.
LT-081: prose back left of a hanging list's labels ends the last item, in its block or the next.
"""

from __future__ import annotations

from typing import Any

from test_source_structure_v8 import _line

from littrans.fidelity import (
    _boundary_space,
    _make_unit,
    _trim_prose_edges,
)
from littrans.source_structure import assemble_structure, plan_structure

# --- LT-060 ------------------------------------------------------------------------------

def test_the_ink_gap_after_a_script_of_the_asset_decides_the_space() -> None:
    script = {"id": "s", "text": "∞", "size": 7.97, "baseline": 96.0}
    word = {"id": "w", "text": "i", "size": 10.9, "baseline": 100.0}
    hyphen = {"id": "h", "text": "-", "size": 10.9, "baseline": 100.0}
    ink = {"s": [100.0, 92.0, 106.0, 97.0], "w": [110.7, 92.0, 113.0, 100.0], "h": [106.8, 96.0, 110.0, 97.0]}
    assert _boundary_space(script, word, ink, notation="left") is True    # C^∞ in: a printed space
    assert _boundary_space(script, hyphen, ink, notation="left") is False  # C^∞-smooth: the script space only
    assert _boundary_space(script, word, ink) is None                      # no side named: as before
    # F_t-adapted: the script space and an italic hyphen's side bearing reach 0.155 em.
    italic = {"id": "k", "text": "-", "size": 10.9, "baseline": 100.0}
    assert _boundary_space(script, italic, {**ink, "k": [107.69, 96.0, 110.0, 97.0]}, notation="left") is False
    # A script of the prose (T_high: the asset is T) is still not comparable.
    base = {"id": "t", "text": "T", "size": 10.9, "baseline": 100.0}
    sub = {"id": "u", "text": "h", "size": 7.97, "baseline": 103.0}
    ink.update({"t": [100.0, 92.0, 106.0, 100.0], "u": [109.0, 97.0, 113.0, 103.0]})
    assert _boundary_space(base, sub, ink, notation="left") is None
    # Before an asset that opens with a script, the same reading applies on the right.
    assert _boundary_space({"id": "w", "text": "e", "size": 10.9, "baseline": 100.0},
                           {"id": "x", "text": "1", "size": 7.97, "baseline": 96.0},
                           {"w": [100.0, 92.0, 104.0, 100.0], "x": [108.0, 92.0, 111.0, 97.0]}, notation="right") is True


# --- LT-067 ------------------------------------------------------------------------------

def _kept(run: list[dict[str, Any]], line: list[dict[str, Any]], following: dict[str, Any] | None = None) -> str:
    return "".join(g["text"] for g in _trim_prose_edges(list(run), line, following=following))


def test_a_closing_exclamation_mark_is_told_from_a_factorial_by_what_follows() -> None:
    # I(f)! closing its paragraph: the exclamation is the sentence's.
    run = _line("I(f)!", ["CMMI10", "CMR10", "CMMI10", "CMR10", "CMR10"], line="b1-l0")
    assert _kept(run, run) == "I(f)"
    # n! followed by a capital opening the next sentence, on the line or the next one.
    line = _line("n! The", ["CMMI10", "CMR10", "CMR10", "CMR10", "CMR10", "CMR10"], line="b2-l0")
    assert _kept(line[:2], line) == "n"
    head = _line("n!", ["CMMI10", "CMR10"], line="b3-l0")
    assert _kept(head, head, following=_line("Then", "CMR10", line="b3-l1", y=34.0)[0]) == "n"
    # A factorial reads on: a lowercase word, a comma, a closing period, another line.
    line = _line("n! ways", ["CMMI10"] + ["CMR10"] * 6, line="b4-l0")
    assert _kept(line[:2], line) == "n!"
    line = _line("n!, and", ["CMMI10"] + ["CMR10"] * 6, line="b5-l0")
    assert _kept(line[:2], line) == "n!"
    run = _line("n!.", ["CMMI10", "CMR10", "CMR10"], line="b6-l0")
    assert _kept(run, run) == "n!"
    assert _kept(head, head, following=_line("ways", "CMR10", line="b3-l1", y=34.0)[0]) == "n!"


# --- LT-081 ------------------------------------------------------------------------------

def _block(bid: str, *lines: list[dict[str, Any]]) -> dict[str, Any]:
    glyphs = [g for line in lines for g in line]
    return {"id": bid, "bbox": [min(g["bbox"][0] for g in glyphs), min(g["bbox"][1] for g in glyphs),
                                max(g["bbox"][2] for g in glyphs), max(g["bbox"][3] for g in glyphs)],
            "lines": [[g["id"] for g in line] for line in lines]}


def _hanging_list(post_in_last_item_block: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Labels of different widths right-aligned on one text column (x = 94), as enumerate sets them.
    intro = _line("Let f satisfy the following conditions:", "CMR10", line="b1-l0", y=100.0, x=40.0)
    first = _line("(i) f is measurable.", "CMR10", line="b2-l0", y=114.0, x=70.0)
    second = _line("(ii) f is adapted.", "CMR10", line="b3-l0", y=128.0, x=64.0)
    third = _line("(iii) f is bounded.", "CMR10", line="b4-l0", y=142.0, x=58.0)
    post = _line("It can be shown that f is simple.", "CMR10", line="b4-l1" if post_in_last_item_block else "b5-l0",
                 y=156.0, x=40.0)
    glyphs = intro + first + second + third + post
    blocks = [_block("b1", intro), _block("b2", first), _block("b3", second)]
    blocks += [_block("b4", third, post)] if post_in_last_item_block else [_block("b4", third), _block("b5", post)]
    return glyphs, blocks


def test_prose_back_left_of_a_hanging_lists_labels_ends_the_last_item() -> None:
    glyphs, blocks = _hanging_list(post_in_last_item_block=True)
    plan = plan_structure(glyphs, blocks, [], 800.0)
    chunks = {chunk["id"]: chunk for chunk in plan["blocks"]}
    assert plan["list_items"]["b4"] == {"label": "(iii)", "body_x": 94.0}
    assert chunks["b4-s2"].get("item_end") is True and "b4-s2" not in plan["list_items"]
    # In a block of its own the prose is flagged too, so assembly never merges it back.
    glyphs, blocks = _hanging_list(post_in_last_item_block=False)
    plan = plan_structure(glyphs, blocks, [], 800.0)
    assert {chunk["id"]: chunk for chunk in plan["blocks"]}["b5"].get("item_end") is True


def test_a_list_that_wraps_to_the_margin_keeps_its_wraps() -> None:
    # Labels set at one x with their text at one column show no hanging list: a line back at
    # the margin after a closed sentence may be the item's own wrap.
    intro = _line("Consider two claims:", "CMR10", line="b1-l0", y=100.0, x=40.0)
    first = _line("(a) The first claim holds.", "CMR10", line="b2-l0", y=114.0, x=58.0)
    wrap = _line("Then the rest follows.", "CMR10", line="b2-l1", y=128.0, x=40.0)
    second = _line("(b) The second claim holds.", "CMR10", line="b3-l0", y=142.0, x=58.0)
    glyphs = intro + first + wrap + second
    blocks = [_block("b1", intro), _block("b2", first, wrap), _block("b3", second)]
    plan = plan_structure(glyphs, blocks, [], 800.0)
    assert not any(chunk.get("item_end") for chunk in plan["blocks"])
    assert [chunk["id"] for chunk in plan["blocks"]] == ["b1", "b2", "b3"]


def test_prose_after_a_hanging_list_stays_a_unit_of_its_own_in_the_same_group() -> None:
    def unit(uid: str, text: str, x: float, y: float) -> Any:
        return _make_unit(2, uid, text, [x, y, 300.0, y + 10.0], {}, kind="paragraph")

    units = [unit("p0002-b1", "Let f satisfy the following conditions:", 40.0, 100.0),
             unit("p0002-b2", "(i) f is measurable.", 70.0, 114.0),
             unit("p0002-b3", "(iii) f is bounded.", 58.0, 128.0),
             unit("p0002-b4", "It can be shown that f is simple.", 40.0, 142.0)]
    plan: dict[str, Any] = {"omitted": {}, "notes": {}, "note_top": 1000.0, "margin": 40.0, "font_size": 10.0,
                            "indent_style": False, "display_blocks": [],
                            "first_x": {"b1": 40.0, "b2": 70.0, "b3": 58.0, "b4": 40.0},
                            "list_items": {"b2": {"label": "(i)", "body_x": 94.0}, "b3": {"label": "(iii)", "body_x": 94.0}}}
    merged = assemble_structure(units, {}, {**plan, "blocks": []}, _make_unit)
    assert [u.unit_id for u in merged] == ["p0002-b1", "p0002-b2", "p0002-b3"]
    result = assemble_structure(units, {}, {**plan, "blocks": [{"id": "b4", "item_end": True}]}, _make_unit)
    assert [u.unit_id for u in result] == ["p0002-b1", "p0002-b2", "p0002-b3", "p0002-b4"]
    # The grouping is the one the prose had before: only the merge is withheld.
    assert result[3].parent_id == result[2].parent_id
