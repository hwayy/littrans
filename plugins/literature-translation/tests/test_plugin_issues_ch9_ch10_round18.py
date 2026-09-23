"""Regressions for PLUGIN-ISSUES.md LT-086 (chapters 9 and 10, round 18).

A detector's heading or caption label stands only where the chunk is set apart from running
text; a list-heavy page keeps its prose margin; and a review packet lists every heading, caption,
run-in label and joined native block for the reviewer to confirm one by one.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pymupdf as fitz
import pytest
from test_fidelity_source import approve

from littrans import fidelity
from littrans.fidelity import (
    _confirmation_failures,
    _make_unit,
    _structure_checks,
    build_source_review_packet,
    import_source_review,
    prepare_source,
)
from littrans.project import ProjectConfig, initialize_project_dirs, save_project
from littrans.source_structure import assemble_structure, detector_role_holds, plan_structure
from littrans.storage import read_json, sha256_file, write_json

FONT = 10.9


def _line(text: str, line: str, y: float, x: float, font: str = "CMR10", size: float = FONT) -> list[dict[str, Any]]:
    return [
        {"id": f"{line}-{i}", "text": c, "font": font, "bbox": [x + i * 5.5, y, x + i * 5.5 + 5.0, y + size],
         "line": line, "size": size, "baseline": y + 8.0, "origin": [x + i * 5.5, y + 8.0]}
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
        block["bbox"] = [min(block["bbox"][0], x), min(block["bbox"][1], y), max(block["bbox"][2], line[-1]["bbox"][2]), max(block["bbox"][3], y + FONT)]
    return glyphs, list(blocks.values())


# --- the prose margin of a list-heavy page ---------------------------------------------

# PDF 235: a closing paragraph, an indented lead-in, then items whose continuation lines at
# the text column (104.3 here) outnumber the prose lines at the margin 58.7.
LIST_PAGE: list[tuple[str, str, float, float]] = [
    ("b4", "A mathematically rigorous introduction of the Gaussian random fields will", 0, 58.7),
    ("b4", "require introducing terminologies in functional analysis and the theory of", 13.4, 58.7),
    ("b4", "will limit ourselves to a more heuristic presentation.", 26.8, 58.7),
    ("b5", "The most important points concerning Gaussian random fields are:", 43.2, 76.6),
    ("b6", "(1) Like most other continuous random fields, Gaussian random fields", 63.2, 82.3),
    ("b7", "are often probability measures on the space of generalized functions", 76.6, 104.3),
    ("b7", "rather than on a space of functions defined pointwise, which is the", 90.0, 104.3),
    ("b8", "(2) A random field is Gaussian if the distributions of the random", 110.0, 82.3),
    ("b9", "vectors of the form below are Gaussian for all choices of the test", 123.4, 104.3),
    ("b9", "functions, and so on for every finite collection of them.", 136.8, 104.3),
    ("b10", "(3) A Gaussian random field is completely determined by its mean", 156.8, 82.3),
    ("b11", "and covariance functions. To define these objects, let psi and phi be", 170.2, 104.3),
    ("b11", "smooth test functions. The mean of a Gaussian random field is a", 183.6, 104.3),
]


def test_a_list_text_column_that_outnumbers_the_prose_is_not_the_margin() -> None:
    glyphs, blocks = _page(LIST_PAGE)
    plan = plan_structure(glyphs, blocks, [], 700)
    assert plan["margin"] == 58.7
    assert plan["indent_style"] is True
    assert plan["first_x"]["b5"] == 76.6
    # The indented lead-in is a paragraph of its own, and the items hang from it.
    texts = {b["id"]: " ".join("".join(g["text"] for g in glyphs if g["id"] in set(line)) for line in b["lines"]) for b in plan["blocks"]}
    units = [_make_unit(235, f"p0235-{b['id']}", texts[b["id"]], b["bbox"], {}) for b in plan["blocks"]]
    result = assemble_structure(units, {}, plan, _make_unit)
    by_id = {u.unit_id: u for u in result}
    assert "p0235-b5" in by_id and not by_id["p0235-b4"].source_text.endswith("are:")
    assert {by_id[f"p0235-b{n}"].parent_id for n in (6, 8, 10)} == {"p0235-b5"}


def test_a_paragraph_indented_inside_an_item_opens_a_paragraph_of_that_item() -> None:
    # PDF 124: "The natural extension ..." is indented from item (1)'s text column; once the
    # margin is the prose margin that indent is no paragraph indent of the page.
    glyphs, blocks = _page([
        ("b2", "Before going further, we discuss briefly the general features of two im-", 0, 58.7),
        ("b2", "portant classes of stochastic processes, the Markov process and the Gauss", 13.4, 58.7),
        ("b3", "There are two ways to think about stochastic processes:", 30, 76.6),
        ("b4", "(1) As random functions of one variable. In this picture, we think", 50, 82.3),
        ("b4", "about the properties of the paths which are individual realizations", 63.4, 104.3),
        ("b5", "The natural extension would be random functions of more than", 76.8, 122.3),
        ("b5", "one variable. These are called random fields. We will come to", 90.2, 104.3),
        ("b6", "(2) As a probability distribution on the path spaces, i.e., spaces", 110, 82.3),
        ("b6", "of functions of one variable, which are infinite-dimensional spaces.", 123.4, 104.3),
    ])
    plan = plan_structure(glyphs, blocks, [], 700)
    assert plan["margin"] == 58.7
    assert plan["list_items"]["b5"] == {"continues": "b4", "paragraph": True}
    texts = {b["id"]: " ".join("".join(g["text"] for g in glyphs if g["id"] in set(line)) for line in b["lines"]) for b in plan["blocks"]}
    units = [_make_unit(124, f"p0124-{b['id']}", texts[b["id"]], b["bbox"], {}) for b in plan["blocks"]]
    by_id = {u.unit_id: u for u in assemble_structure(units, {}, plan, _make_unit)}
    # The lead-in, both items and the item's own paragraph stay distinct units of one list.
    assert list(by_id) == ["p0124-b2", "p0124-b3", "p0124-b4", "p0124-b5", "p0124-b6"]
    assert {by_id[f"p0124-b{n}"].parent_id for n in (4, 5, 6)} == {"p0124-b3"}


def test_a_page_of_exercises_without_prose_beside_them_keeps_the_item_column() -> None:
    spec = [
        ("b0", "1.1. Let X be a variable with a Bernoulli", 0, 79.1),
        ("b0", "distribution with parameter p and compute", 12, 101.0),
        ("b0", "its mean and its variance.", 24, 101.0),
        ("b1", "1.2. Let S be the number of heads in n", 40, 79.1),
        ("b1", "tosses of a fair coin, and find its law.", 52, 101.0),
        ("b2", "12", 70, 58.7),
    ]
    glyphs, blocks = _page(spec)
    assert plan_structure(glyphs, blocks, [], 700)["margin"] == 101.0


# --- detector heading and caption labels need typography -------------------------------

def _run(parts: Sequence[tuple[str, str]], x: float = 58.7, size: float = FONT) -> list[dict[str, Any]]:
    glyphs: list[dict[str, Any]] = []
    for text, font in parts:
        glyphs += _line(text, "b0-l0", 0, x + len(glyphs) * 5.5, font=font, size=size)
    return glyphs


def test_a_title_label_on_running_text_is_prose() -> None:
    def heading(parts: Sequence[tuple[str, str]], x: float = 58.7, size: float = FONT) -> bool:
        return detector_role_holds("heading", _run(parts, x, size), x, 58.7, FONT, "CMR10", "".join(p for p, _ in parts))

    # PDF 225: an italic step line at the paragraph indent; PDF 229: a bold run-in label
    # followed by the statement on the same line.
    assert not heading([("Step 2. Infinite-dimensional analog.", "CMTI10")], x=76.6)
    assert not heading([("Theorem 9.2", "CMBX10"), (" (Girsanov Theorem I). ", "CMR10"), ("Consider the Ito process", "CMTI10")])
    # Set apart by size, a bold or sans face of its own, capitals, small capitals or position.
    assert heading([("10.2. Gaussian Random Fields", "CMBX12")], size=FONT * 1.1)
    assert heading([("1.1. Origin and justification", "SFBX1000")])
    assert heading([("Introduction", "Helvetica")])
    assert heading([("CHAPTER 1", "CMR10")])
    assert heading([("Notes and remarks", "CMCSC10")])
    assert heading([("A centred title", "CMR10")], x=180.0)


def test_a_caption_label_on_a_sentence_about_the_figure_is_prose() -> None:
    def caption(parts: Sequence[tuple[str, str]], x: float = 58.7, size: float = FONT) -> bool:
        return detector_role_holds("caption", _run(parts, x, size), x, 58.7, FONT, "CMR10", "".join(p for p, _ in parts))

    # PDF 234: body text at the margin that mentions the figure.
    assert not caption([("Figure 10.2 shows an image degraded by adding Gaussian noise.", "CMR10")])
    assert caption([("Figure 10.2.", "CMBX9"), (" The original tower image.", "CMR9")], size=FONT * 0.82)
    assert caption([("Figure 1.1.", "SFCC1000"), (" Quantum advantage hierarchy.", "SFRM1000")])
    assert caption([("Trajectory of the differential equation", "CMR10")], x=110.0)
    assert caption([("Figure 3. A plain caption set at the margin.", "CMR10")])
    assert caption([("Table 2: Measured values.", "CMR10")])


def test_a_title_box_over_running_text_does_not_hold_the_block_together() -> None:
    glyphs, blocks = _page([
        ("b0", "Step 2. Infinite-dimensional analog. Formally, as we take the", 0, 76.6),
        ("b0", "limit, the matrix converges to the differential operator.", 13.4, 58.7),
        ("b0", "An indented new paragraph starts here and runs on.", 26.8, 76.6),
        ("b1", "Another paragraph at the margin closes the page.", 45, 58.7),
        ("b1", "It has a second line to settle the margin.", 58.4, 58.7),
    ])
    for g in glyphs:
        if g["line"] == "b0-l0":
            g["font"] = "CMTI10"
    title = {"label": "paragraph_title", "bbox": [100, -10, 800, 90], "score": 0.7}
    plan = plan_structure(glyphs, blocks, [title], 700)
    assert [b["id"] for b in plan["blocks"]] == ["b0", "b0-s2", "b1"]


# --- per-unit confirmation of roles and joined blocks -----------------------------------

def _packet_page(overruled: dict[str, str] | None = None) -> dict[str, Any]:
    glyphs = (_line("Theorem 9.2 Consider", "b1-l0", 100, 58.7) + _line("the process.", "b2-l0", 113, 58.7)
              + _line("Figure 10.1. A model.", "b3-l0", 300, 94.5) + _line("10", "b4-l0", 400, 150, font="CMMI10"))
    for g in glyphs:
        g["owner"] = "native-text"
    units = [
        {"unit_id": "p0229-b1", "kind": "paragraph", "source_text": "**Theorem 9.2** Consider the process.", "bbox": [58.7, 100, 300, 124], "render_policy": "include"},
        {"unit_id": "p0229-b3", "kind": "caption", "source_text": "**Figure 10.1.** A model.", "bbox": [94.5, 300, 300, 311], "render_policy": "include"},
        {"unit_id": "p0229-b0", "kind": "note", "source_text": "running head", "bbox": [0, 0, 1, 1], "render_policy": "omit"},
    ]
    structure: dict[str, Any] = {"omitted": {"b0": "running-header-or-footer"}}
    if overruled:
        structure["overruled_labels"] = overruled
    return {"page": 229, "units": units, "ledger": {"glyphs": glyphs, "structure": structure}}


def test_structure_checks_list_roles_and_joined_blocks() -> None:
    checks = _structure_checks(_packet_page({"b1": "paragraph_title"}))
    assert [(r["unit_id"], r["kind"], r.get("detector_label")) for r in checks["roles"]] == [
        ("p0229-b1", "paragraph", "paragraph_title"), ("p0229-b3", "caption", None)]
    # b2's text went into b1; b4 holds no language and is no join.
    assert checks["joins"] == [{"block": "b2", "unit_id": "p0229-b1", "text": "the process."}]


def test_every_listed_role_and_join_needs_its_own_confirmation() -> None:
    page = _packet_page()
    page["structure_checks"] = _structure_checks(page)
    assert _confirmation_failures(page, {}) == ["role-unconfirmed: p0229-b1, p0229-b3", "join-unconfirmed: b2"]
    decision = {"confirmed_roles": [{"unit_id": "p0229-b1", "kind": "paragraph"}, {"unit_id": "p0229-b3", "kind": "paragraph"}],
                "confirmed_joins": [{"block": "b2"}]}
    assert _confirmation_failures(page, decision) == [
        "role-disputed: p0229-b3 is recorded as caption, the review reads paragraph; correct it with a units override"]
    decision["confirmed_roles"][1]["kind"] = "caption"
    assert _confirmation_failures(page, decision) == []
    decision["confirmed_roles"].append({"unit_id": "p0229-b9", "kind": "heading"})
    assert _confirmation_failures(page, decision) == ["role-not-listed: p0229-b9"]
    with pytest.raises(ValueError, match="confirmed_roles must be a list of objects"):
        _confirmation_failures(page, {"confirmed_roles": ["p0229-b1"]})
    # A packet made before the lists existed asks for nothing new: its receipts keep passing.
    del page["structure_checks"]
    assert _confirmation_failures(page, {}) == []


@pytest.fixture
def statement_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {"status": "unavailable", "reason": "isolated test", "pages": {}})
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 80), "Theorem 1. Every bounded sequence has a convergent subsequence.")
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="t", title="t", source_path="source/book.pdf", source_sha256=sha256_file(pdf), source_pages=1, profile="technical-book"))
    prepare_source(tmp_path, "1", allow_missing_layout=True)
    return tmp_path


def test_a_review_that_ticks_the_flags_but_skips_a_listed_role_is_rejected(statement_page: Path) -> None:
    packet = build_source_review_packet(statement_page, "1")
    review = read_json(Path(packet["review_template"]))
    page = review["pages"][0]
    assert page["confirmed_roles"] == [] and page["confirmed_joins"] == []
    roles = page["context"]["structure_checks"]["roles"]
    assert [r["kind"] for r in roles] == ["paragraph"] and roles[0]["text"].startswith("Theorem 1.")
    review["reviewer"] = "reviewer"
    for key in ("viewed_original", "coverage_complete", "boundaries_complete", "reading_order_correct", "grouping_checked", "layout_fallback_checked"):
        page[key] = True
    write_json(statement_page / "review.json", review)
    result = import_source_review(statement_page, statement_page / "review.json", True)
    assert result["approved_pages"] == [] and "role-unconfirmed" in str(result["rejected_pages"])
    # The shared test reviewer confirms what the context lists.
    assert approve(statement_page, "1")["approved_pages"] == [1]
