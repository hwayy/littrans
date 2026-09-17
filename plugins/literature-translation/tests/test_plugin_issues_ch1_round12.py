"""Round 12 of the chapter-1 ledger: LT-041..LT-045.

Two structure defects found while verifying a second chapter (a paragraph opening after a
list item's continuation line, an unreferenced figure sorted to the page end by the
detached page number) and three record defects that surfaced once the project moved to a
second host: receipts bound to the profile file's bytes, receipts bound to the whole
profile so that extending it for a new chapter voids the previous one, and absolute
``file://`` links in rendered checkpoints. Synthetic pages only; nothing here is evidence
about a particular document.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pymupdf as fitz
import pytest
from test_source_structure_v9 import GAP, MARGIN, OPENING, PITCH, _breaks, _page

from littrans.fidelity import (
    build_source_review_packet,
    import_source_review,
    prepare_source,
    verify_fidelity,
)
from littrans.models import ProjectConfig, SourceUnit, UnitKind
from littrans.source_structure import plan_structure
from littrans.storage import (
    initialize_project_dirs,
    read_json,
    read_jsonl,
    save_project,
    sha256_file,
    write_json,
)
from littrans.structure_profile import (
    PROFILE_PATH,
    guidance_difference,
    page_guidance,
    probe_structure,
    structure_context,
)

# ---------------------------------------------------------------------------------------
# LT-044: the paragraph after a list item opens after the item's continuation line.
# ---------------------------------------------------------------------------------------


def test_a_paragraph_after_a_list_items_continuation_line_breaks() -> None:
    # ``b)`` is not a printed label the planner recognises; its continuation sits at the
    # item's text column (3.9em right of the margin), which is a text column all the same.
    spec = [
        *OPENING,
        ("b1", "b) an item that wraps onto", 3 * PITCH, MARGIN + 15),
        ("b1", "a second line and ends here.", 4 * PITCH, MARGIN + 39),
        ("b2", "A new paragraph back at the margin.", 4 * PITCH + GAP, MARGIN),
    ]
    assert _breaks(spec) == ["b2"]
    # A recognised label whose nested continuation column lies beyond the text-column
    # window is text because the planner placed it as a list item's continuation.
    nested = [
        *OPENING,
        ("b1", "(a) an item whose text column", 3 * PITCH, MARGIN + 15),
        ("b1", "sits far to the right and ends.", 4 * PITCH, MARGIN + 30),
        ("b2", "(i) a nested item wrapping to a", 5 * PITCH, MARGIN + 30),
        ("b3", "column beyond the window, closing here.", 6 * PITCH, MARGIN + 54),
        ("b4", "A new paragraph back at the margin.", 6 * PITCH + GAP, MARGIN),
    ]
    glyphs, blocks = _page(nested)
    plan = plan_structure(glyphs, blocks, [], 700)
    assert plan["list_items"].get("b3") == {"continues": "b2"}
    assert [chunk["id"] for chunk in plan["blocks"] if chunk.get("paragraph_break")] == ["b4"]
    # A row set right of every text column is still a display, whatever it says.
    assert _breaks([*OPENING, ("b1", "dt + {terms of order two}.", 3 * PITCH, 150), ("b2", "Here we used the fact.", 3 * PITCH + GAP, MARGIN)]) == []


# ---------------------------------------------------------------------------------------
# LT-045: an unreferenced figure keeps its reading position when a page number is detached.
# ---------------------------------------------------------------------------------------


@pytest.fixture
def numbered_figure_project(tmp_path: Path) -> Path:
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=400)
        # Running head and page number on one line: the number is detached as a chunk of
        # its own and appended after the body.
        page.insert_text((40, 30), "1. Random Variables")
        page.insert_text((250, 30), "7")
        page.insert_text((40, 70), "Prose before the figure.")
        page.draw_line((60, 100), (240, 160), width=1.5)
        page.draw_line((60, 160), (240, 100), width=1.5)
        page.insert_text((80, 185), "Caption of the drawing")
        page.insert_text((40, 220), "Prose after the figure.")
        page.insert_text((40, 240), "More prose at the end of the page.")
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="fig", title="fig", source_path="source/book.pdf",
                                         source_sha256=sha256_file(pdf), source_pages=1, profile="technical-book"))
    return tmp_path


def test_an_unreferenced_figure_sorts_before_its_caption_not_after_the_page_number(numbered_figure_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.fidelity as fidelity

    def fake_detect(images: list[Path], output: Path) -> dict[str, Any]:
        page = str(images[0].resolve())
        return {"status": "ok", "fingerprint": "figure-number-test", "pages": {page: [
            {"label": "header", "bbox": [70, 40, 400, 70], "score": 0.9},
            {"label": "number", "bbox": [490, 40, 520, 70], "score": 0.9},
            {"label": "image", "bbox": [110, 190, 490, 330], "score": 0.9},
            {"label": "figure_title", "bbox": [150, 350, 420, 385], "score": 0.9},
        ]}}

    monkeypatch.setattr(fidelity, "detect_layout", fake_detect)
    prepare_source(numbered_figure_project)
    ledger = read_json(numbered_figure_project / "derived/fidelity-pages/p0001.json")
    detached = [bid for bid in ledger["structure"]["omitted"] if bid.endswith("-pagenum")]
    assert detached, "the page number must be detached from the running head for this test to mean anything"
    units = read_jsonl(numbered_figure_project / "derived/units.jsonl", SourceUnit)
    figure = next(u for u in units if u.kind is UnitKind.FIGURE)
    caption = next(u for u in units if u.kind is UnitKind.CAPTION)
    order = [u.unit_id for u in units]
    before = next(u for u in units if "before the figure" in u.source_text)
    after = next(u for u in units if "after the figure" in u.source_text)
    assert order.index(before.unit_id) < order.index(figure.unit_id) < order.index(caption.unit_id) < order.index(after.unit_id)
    assert caption.parent_id == figure.unit_id
    assert ledger["unit_ids"] == order


# ---------------------------------------------------------------------------------------
# LT-041 / LT-043: receipts bind the guidance that applies to their page, not the file.
# ---------------------------------------------------------------------------------------


def _two_page_project(tmp_path: Path) -> Path:
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        for number in (1, 2):
            page = doc.new_page(width=300, height=400)
            page.insert_text((40, 60), f"Chapter {number} opens with prose.")
            page.insert_text((40, 80), "A second line of the paragraph ends here.")
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="two", title="two", source_path="source/book.pdf",
                                         source_sha256=sha256_file(pdf), source_pages=2, profile="technical-book"))
    (tmp_path / "context/document-brief.md").write_text("# Brief", encoding="utf-8")
    (tmp_path / "context/style-guide.md").write_text("# Style", encoding="utf-8")
    return tmp_path


def _approve(root: Path, spec: str) -> dict[str, Any]:
    """Approve the pages through the oracle reviewer; returns the packet they are bound to."""
    from test_fidelity_source import approve

    approve(root, spec)
    return build_source_review_packet(root, spec)


def _profile(root: Path) -> dict[str, Any]:
    return read_json(root / PROFILE_PATH)


def _write_profile(root: Path, profile: dict[str, Any]) -> None:
    write_json(root / PROFILE_PATH, profile)


@pytest.fixture
def reviewed_page(tmp_path: Path) -> Path:
    root = _two_page_project(tmp_path)
    probe_structure(root, "1")
    profile = _profile(root)
    profile["handling_rules"] = {"lists": "Labels are printed (a), (b).", "headings": "Chapter openers are italic."}
    profile["review_notes"] = "Inspected page 1."
    profile["inspected_pages"] = [1]
    profile["status"] = "reviewed"
    _write_profile(root, profile)
    prepare_source(root, "1", allow_missing_layout=True)
    _approve(root, "1")
    assert verify_fidelity(root, "1")["passed"]
    return root


def test_receipts_survive_a_rewrite_of_the_profile_file_bytes(reviewed_page: Path) -> None:
    root = reviewed_page
    digest = structure_context(root)["sha256"]
    path = root / PROFILE_PATH
    profile = json.loads(path.read_bytes().decode("utf-8"))
    reordered = {key: profile[key] for key in reversed(list(profile))}
    path.write_bytes(json.dumps(reordered, ensure_ascii=False, indent=4).replace("\n", "\r\n").encode("utf-8"))
    assert b"\r\n" in path.read_bytes()
    assert structure_context(root)["sha256"] == digest
    result = verify_fidelity(root, "1")
    assert result["passed"], result["errors"]


def test_extending_the_profile_for_new_pages_keeps_earlier_receipts(reviewed_page: Path) -> None:
    root = reviewed_page
    probe_structure(root, "2")
    assert _profile(root)["status"] == "draft"
    assert verify_fidelity(root, "1")["passed"]
    profile = _profile(root)
    profile["page_rules"] = [{"label": "Chapter 2", "pages": "2", "handling_rules": {"lists": "- Chapter 2 adds a) labels."}}]
    profile["inspected_pages"] = [1, 2]
    profile["review_notes"] += " Inspected page 2."
    profile["status"] = "reviewed"
    _write_profile(root, profile)
    result = verify_fidelity(root, "1")
    assert result["passed"], result["errors"]
    dump = structure_context(root)["profile"]
    assert page_guidance(dump, 2)["lists"] == "Labels are printed (a), (b).\n- Chapter 2 adds a) labels."
    assert page_guidance(dump, 1)["lists"] == "Labels are printed (a), (b)."
    # A packet built now carries the blocks; page 2 reviewed under them verifies.
    prepare_source(root, "2", allow_missing_layout=True)
    packet = _approve(root, "2")
    assert read_json(Path(packet["packet_path"]))["document_structure"]["profile"]["page_rules"] == profile["page_rules"]
    assert verify_fidelity(root, "1-2")["passed"]
    ledger = read_json(root / "derived/fidelity-pages/p0002.json")
    assert set(ledger["structure"]["document_profile"]) == {"path", "guidance_sha256", "authority"}
    assert ledger["structure"]["document_profile"]["guidance_sha256"] != read_json(root / "derived/fidelity-pages/p0001.json")["structure"]["document_profile"]["guidance_sha256"]


def test_a_base_rule_split_into_a_scoped_block_reads_the_same(reviewed_page: Path) -> None:
    root = reviewed_page
    profile = _profile(root)
    profile["handling_rules"]["lists"] = "Labels are printed (a), (b).\n- Page one also prints bullets."
    _write_profile(root, profile)
    prepare_source(root, "1", replace=True, allow_missing_layout=True)
    _approve(root, "1")
    assert verify_fidelity(root, "1")["passed"]
    profile["handling_rules"]["lists"] = "Labels are printed (a), (b)."
    profile["page_rules"] = [{"pages": "1", "handling_rules": {"lists": "- Page one also prints bullets."}}]
    _write_profile(root, profile)
    assert verify_fidelity(root, "1")["passed"]
    profile["pages"] = [1, 2]
    profile["page_rules"][0]["pages"] = "2"
    _write_profile(root, profile)
    result = verify_fidelity(root, "1")
    assert not result["passed"]
    assert "guidance changed since review for page 1 (handling_rules: lists)" in result["errors"][0]["message"]


def test_rescope_moves_rule_text_appended_in_place_into_a_block(reviewed_page: Path) -> None:
    """The pilot's situation: base rules extended for chapter 2, both chapters reviewed."""
    from typer.testing import CliRunner

    from littrans import cli
    from littrans.structure_profile import rescope_rules

    root = reviewed_page
    first_packet = read_json(root / "evidence/pages/fidelity-p0001.review.json")["packet_id"]
    probe_structure(root, "2")
    profile = _profile(root)
    profile["handling_rules"]["lists"] += "\n- Chapter 2 adds a) labels."
    profile["handling_rules"]["headings"] += "\n- Chapter 2 repeats the opener form."
    profile["review_notes"] += " Inspected page 2."
    profile["inspected_pages"] = [1, 2]
    profile["status"] = "reviewed"
    _write_profile(root, profile)
    prepare_source(root, "2", allow_missing_layout=True)
    _approve(root, "2")
    result = verify_fidelity(root, "1-2")
    assert sorted({e["page"] for e in result["errors"]}) == [1]
    # A dry run reports the move without writing; the block pages may not include the packet's own.
    preview = rescope_rules(root, first_packet, "2", "Chapter 2", apply=False)
    assert preview["scoped_rules"] == ["headings", "lists"] and preview["unchanged_rules"] == [] and not preview["applied"]
    assert _profile(root)["page_rules"] == []
    with pytest.raises(ValueError, match="must not include pages the packet reviews"):
        rescope_rules(root, first_packet, "1-2")
    runner = CliRunner()
    run = runner.invoke(cli.app, ["source", "rescope", str(root), "--packet", first_packet, "--pages", "2", "--label", "Chapter 2"])
    assert run.exit_code == 0, run.output
    profile = _profile(root)
    assert profile["handling_rules"]["lists"] == "Labels are printed (a), (b)."
    assert profile["page_rules"] == [{"label": "Chapter 2", "pages": "2", "handling_rules": {
        "lists": "- Chapter 2 adds a) labels.", "headings": "- Chapter 2 repeats the opener form."}}]
    assert verify_fidelity(root, "1-2")["passed"]
    # Refusals: nothing appended, a rewritten rule, a rule the packet does not know.
    with pytest.raises(ValueError, match="nothing to scope"):
        rescope_rules(root, first_packet, "2")
    profile["handling_rules"]["lists"] = "Labels are printed 1., 2."
    _write_profile(root, profile)
    with pytest.raises(ValueError, match="rewritten, not extended"):
        rescope_rules(root, first_packet, "2")
    profile["handling_rules"]["lists"] = "Labels are printed (a), (b).\nmore"
    profile["handling_rules"]["figures"] = "Captions below."
    _write_profile(root, profile)
    with pytest.raises(ValueError, match="not in the packet"):
        rescope_rules(root, first_packet, "2")


def test_editing_a_base_rule_names_the_page_and_the_keys(reviewed_page: Path) -> None:
    root = reviewed_page
    packet = build_source_review_packet(root, "1")
    review = read_json(Path(packet["review_template"]))
    profile = _profile(root)
    profile["handling_rules"]["lists"] = "Labels are printed 1., 2."
    profile["handling_rules"]["figures"] = "Captions sit below."
    _write_profile(root, profile)
    result = verify_fidelity(root, "1")
    assert not result["passed"]
    assert result["errors"][0]["code"] == "fidelity-source-unverified"
    assert result["errors"][0]["message"] == "source structure guidance changed since review for page 1 (handling_rules: figures, lists)"
    review["reviewer"] = "tester"
    with pytest.raises(ValueError, match=r"guidance changed since packet creation for page 1 \(handling_rules: figures, lists\)"):
        import_source_review(root, _write_review(root, review), confirm_visual_review=True)


def _write_review(root: Path, review: dict[str, Any]) -> Path:
    path = root / "reviews" / "review.json"
    write_json(path, review)
    return path


def test_page_rules_blocks_are_validated(reviewed_page: Path) -> None:
    root = reviewed_page
    profile = _profile(root)
    profile["page_rules"] = [{"pages": "all", "handling_rules": {"lists": "x"}}]
    _write_profile(root, profile)
    with pytest.raises(ValueError, match="page_rules block pages"):
        structure_context(root)
    profile["page_rules"] = [{"pages": "2", "handling_rules": {"lists": "x"}}]
    _write_profile(root, profile)
    with pytest.raises(ValueError, match="page_rules block pages"):
        structure_context(root)
    profile["page_rules"] = [{"pages": "1", "handling_rules": {}}]
    _write_profile(root, profile)
    with pytest.raises(ValueError):
        structure_context(root)


def test_guidance_difference_distinguishes_added_removed_and_changed() -> None:
    before = {"profile": {"pages": [1, 2], "handling_rules": {"a": "one", "b": "two"}}}
    after = {"profile": {"pages": [1, 2], "handling_rules": {"a": "one", "b": "three"}, "page_rules": [{"pages": "2", "handling_rules": {"c": "new"}}]}}
    assert guidance_difference(before, before, 1) is None
    assert guidance_difference(None, before, 1) == "for page 1 (profile added)"
    assert guidance_difference(before, None, 1) == "for page 1 (profile removed)"
    assert guidance_difference(before, after, 1) == "for page 1 (handling_rules: b)"
    assert guidance_difference(before, after, 2) == "for page 2 (handling_rules: b, c)"
    assert page_guidance({"pages": [1], "handling_rules": {"a": "", "b": "  "}}, 1) == {}


def test_batch_context_lists_only_the_blocks_covering_the_batch(reviewed_page: Path) -> None:
    from littrans.batching import create_batches

    root = reviewed_page
    probe_structure(root, "2")
    profile = _profile(root)
    profile["page_rules"] = [{"label": "Chapter 2", "pages": "2", "handling_rules": {"lists": "- Chapter 2 adds a) labels."}}]
    profile["status"] = "reviewed"
    profile["inspected_pages"] = [1, 2]
    _write_profile(root, profile)
    prepare_source(root, "2", allow_missing_layout=True)
    _approve(root, "2")
    first = (root / "batches" / create_batches(root, "1")[0].batch_id / "context.md").read_text(encoding="utf-8")
    assert "page_rules" not in first
    second = (root / "batches" / create_batches(root, "2")[0].batch_id / "context.md").read_text(encoding="utf-8")
    assert "page_rules" in second and "Chapter 2 adds" in second


# ---------------------------------------------------------------------------------------
# LT-042: rendered checkpoints do not depend on where the tree lives.
# ---------------------------------------------------------------------------------------


def test_rendered_checkpoints_are_identical_under_different_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.fidelity as fidelity
    from littrans.rendering import render_project
    from littrans.source_render import render_source_review

    root = _two_page_project(tmp_path / "a")
    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {"status": "ok", "fingerprint": "x", "pages": {}})
    prepare_source(root, "1")
    _approve(root, "1")
    other = tmp_path / "a-much-longer-root-name-for-the-same-tree"
    shutil.copytree(root, other)
    pages = []
    for tree in (root, other):
        source = Path(render_source_review(tree, "1", "checkpoint")["html"]).read_bytes()
        bilingual = Path(render_project(tree, "1", name="reading", allow_draft=True)["html"]).read_bytes()
        pages.append((source, bilingual))
    assert pages[0] == pages[1]
    source_html = pages[0][0].decode("utf-8")
    assert "file:///" not in source_html and "file:///" not in pages[0][1].decode("utf-8")
    assert 'href="../evidence/pages/fidelity-p0001.png"' in source_html
    assert 'href="../source/book.pdf"' in source_html
    assert "../source/book.pdf#page=1" in pages[0][1].decode("utf-8")


def test_a_pdf_outside_the_project_keeps_a_file_uri(tmp_path: Path) -> None:
    from littrans.representations import portable_href

    root = tmp_path / "project"
    output = root / "output"
    assert portable_href(root / "source" / "my book.pdf", output, root) == "../source/my%20book.pdf"
    assert portable_href(tmp_path / "elsewhere.pdf", output, root) == (tmp_path / "elsewhere.pdf").resolve().as_uri()
