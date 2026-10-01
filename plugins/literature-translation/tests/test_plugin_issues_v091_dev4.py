"""Source review regressions use synthetic geometry, never book-specific IDs."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pymupdf as fitz
import pytest
from test_source_structure_v8 import _line

from littrans import fidelity
from littrans.scaffold import scaffold_project
from littrans.source_structure import list_label, plan_structure


def test_one_sided_letter_labels_are_lists_but_function_calls_are_not() -> None:
    assert list_label(_line("a) first", "CMR10"), 10) is not None
    assert list_label(_line("iv) fourth", "CMR10"), 10) is not None
    assert list_label(_line("f(x) first", "CMR10"), 10) is None


def test_math_face_probability_abbreviation_is_declared_language() -> None:
    glyphs = _line("x=0 a.s.", "CMMI10")
    conditions = fidelity._auto_formula_conditions({"glyph_ids": [g["id"] for g in glyphs]}, {g["id"]: g for g in glyphs})
    assert [c["source_text"] for c in conditions] == ["a.s."]


def test_word_subscript_is_not_flattened_but_prose_footnote_stays_text() -> None:
    base = _line("T", "CMMI10", y=20)
    sub = _line("high", "CMR8", line="b0-l0", y=23, size=8, x=6)
    for g in sub:
        g["id"] += "sub"
    assert fidelity._script_digit_ids({"b0-l0": base + sub}) == {g["id"] for g in sub}
    base[0]["font"] = "CMR10"
    assert fidelity._script_digit_ids({"b0-l0": base + sub}) == set()


def test_sentence_after_display_returns_to_prose() -> None:
    glyphs = _line("x=y. Then z holds", ["CMMI10"]*3 + ["CMR10"]*14)
    prose = {g["id"] for g in glyphs[5:]}
    kept = fidelity._strip_display_prose(glyphs, prose)
    assert "".join(g["text"] for g in kept) == "x=y"


def test_recovered_italic_prose_rejoins_across_native_blocks() -> None:
    from collections import Counter
    assert fidelity._rejoin_line_breaks("*has the repre-*\n*sentation*", (Counter(), Counter())) == "*has the representation*"


def test_cross_page_container_suggestion_does_not_reparent_source() -> None:
    first = fidelity._make_unit(1, "p0001-b0", "Theorem 1. Two claims:", [0, 0, 100, 20], {}, parent_id="p0001-b0")
    tail = fidelity._make_unit(1, "p0001-b1", "(a) First claim", [0, 30, 100, 50], {}, parent_id=first.unit_id)
    next_ = fidelity._make_unit(2, "p0002-b0", "(b) Second claim", [0, 0, 100, 20], {}, parent_id="p0002-b0")
    result = fidelity._cross_page_container_candidates([first, tail, next_], 2)
    assert result[0]["candidate_parent_id"] == first.unit_id
    assert next_.parent_id == next_.unit_id
    next_.source_text = "Proof. New proof"
    assert fidelity._cross_page_container_candidates([first, tail, next_], 2) == []


def test_source_render_loads_registry_once_per_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from littrans import representations
    from littrans.source_render import _unit_body
    calls = []
    monkeypatch.setattr(representations, "_assets", lambda root: calls.append(root) or {"a": {}})
    monkeypatch.setattr(representations, "_asset_html", lambda *args, **kwargs: "<img>")
    cache = representations.AssetRenderCache(tmp_path)
    from littrans.models import SourceUnit
    unit = SourceUnit(unit_id="p0001-b0", page=1, source_text="{{asset:a}}", source_markdown="{{asset:a}}",
                      source_hash="a"*64, bbox=(0, 0, 20, 20), kind="paragraph", confidence=1)
    assert [_unit_body(tmp_path, unit, tmp_path, cache=cache) for _ in range(3)] == ["<p><img></p>"]*3
    assert calls == [tmp_path]


def test_chapter_number_above_large_title_is_not_running_header() -> None:
    label = _line("Chapter 7", "CMTI10", x=200, y=40)
    title = _line("Limit theorems", "CMBX18", y=70, size=18)
    body = _line("This is ordinary body text with enough letters to establish the body size.", "CMR10", y=120)
    glyphs = label + title + body
    blocks = [{"id": f"b{i}", "bbox": fidelity._union([g["bbox"] for g in row]),
               "lines": [[g["id"] for g in row]]} for i, row in enumerate([label, title, body])]
    # Unique native IDs per block.
    for i, row in enumerate([label, title, body]):
        for j, g in enumerate(row):
            g["id"], g["line"] = f"b{i}-l0-{j}", f"b{i}-l0"
        blocks[i]["lines"] = [[g["id"] for g in row]]
    layout = [{"label": "header", "bbox": [v*2 for v in blocks[0]["bbox"]]},
              {"label": "doc_title", "bbox": [v*2 for v in blocks[1]["bbox"]]}]
    plan = plan_structure(glyphs, blocks, layout, 800)
    assert "b0" not in plan["omitted"]
    assert "b0" in plan["chapter_headings"]
    plan = plan_structure(glyphs, blocks, layout[:1], 800)
    assert plan["omitted"]["b0"] == "running-header-or-footer"


def test_bottom_rule_grows_table_but_distant_ornament_does_not() -> None:
    glyphs = _line("row values", "CMR10", x=10, y=15)
    box = [10., 10., 100., 50.]
    assert fidelity._grow_table_header(box, glyphs, 0, [[10, 52, 100, 52]])[3] == 52.5
    assert fidelity._grow_table_header(box, glyphs, 0, [[10, 65, 100, 65]]) == box


def test_native_caption_trim_preserves_internal_plot_labels() -> None:
    box = [0., 0., 300., 200.]
    caption = _line("Figure 2.1: Caption text", "CMR10", y=185)
    assert fidelity._exclude_native_caption(box, caption)[3] == 184.5
    label = _line("Figure 2.1: Interior panel label", "CMR10", y=50)
    assert fidelity._exclude_native_caption(box, label) == box


def test_mixed_figure_table_panels_share_caption_but_not_table_caption() -> None:
    regions = [{"kind": kind, "bbox": box, "display": True, "provenance": [], "grouping_pending": False}
               for kind, box in [("table", [0, 0, 100, 100]), ("figure", [110, 0, 210, 100])]]
    caption = {"label": "figure_caption", "bbox": [0, 210, 420, 230]}
    joined = fidelity._join_figure_panels(regions, [], [caption])
    assert len(joined) == 1 and joined[0]["kind"] == "figure"
    assert len(joined[0]["fragments"]) == 2
    own = {"label": "table_caption", "bbox": [0, 204, 200, 208]}
    assert len(fidelity._join_figure_panels(regions, [], [caption, own])) == 2


def test_three_rows_share_middle_label_without_absorbing_second_equation() -> None:
    rows = [_line("x=y", "CMMI10", line=f"b{i}-l0", y=y, x=40) for i, y in enumerate([20, 35, 50])]
    tag = _line("(2.7)", "CMR10", line="b3-l0", y=35, x=0)
    regions = [{"kind": "math", "display": True, "bbox": fidelity._union([g["bbox"] for g in row]),
                "glyph_ids": [g["id"] for g in row], "provenance": [], "grouping_pending": False} for row in rows]
    glyphs = sum(rows, []) + tag
    joined = fidelity._join_tag_split_display(regions, glyphs, {g["id"] for g in tag})
    assert len(joined) == 1 and len(joined[0]["fragments"]) == 3
    other = _line("(2.8)", "CMR10", line="b4-l0", y=50, x=0)
    assert len(fidelity._join_tag_split_display(regions, glyphs + other, {g["id"] for g in tag + other})) == 3


@pytest.mark.parametrize("missing,draw", [(False, False), (True, False), (False, True)])
def test_fallback_uses_complete_ink_and_keeps_unmeasured_graphics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                              missing: bool, draw: bool) -> None:
    from littrans import glyph_export
    with fitz.open() as doc:
        page = doc.new_page(width=100, height=100)
        page.insert_text((20, 30), "x")
        if draw:
            page.draw_line((30, 25), (34, 25))
        glyphs, _ = fidelity._native(page)
        monkeypatch.setattr(glyph_export, "glyph_ink_boxes", lambda *_: {} if missing else {glyphs[0]["id"]: [20, 20, 25, 30]})
        padded = fitz.Rect(19, 19, 35, 31)
        result = fidelity._raw_fallback_rect(page, {"bbox": [20, 20, 26, 30]}, glyphs, glyphs, padded)
        assert result == (padded if missing or draw else fitz.Rect(19.5, 19.5, 26, 30.5))


def test_scaffold_preserves_task_bytes_through_git_checkout(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    scaffold_project(root / "workspace", repo_root=root)
    first = (root / ".gitattributes").read_bytes()
    scaffold_project(root / "workspace", repo_root=root)
    assert (root / ".gitattributes").read_bytes() == first
    task = root / "workspace/.littrans/work/tasks/task-test"
    task.mkdir(parents=True)
    raw = b'{\r\n  "verified": true\r\n}\r\n'
    for name in ["result.json", "inspection-evidence.json"]:
        (task / name).write_bytes(raw)
    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)
    git("init")
    git("add", ".gitattributes", "workspace/.littrans/work/tasks")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    for name in ["result.json", "inspection-evidence.json"]:
        relative = f"workspace/.littrans/work/tasks/task-test/{name}"
        assert git("show", f"HEAD:{relative}") == raw
        (task / name).unlink()
        git("checkout", "--", relative)
        assert (task / name).read_bytes() == raw
