"""Regressions LT-009 to LT-015 recorded while translating chapter 1 of a TeX-set book.

Regex glossary patterns must fold like substrings, native-text display lines must
render as text and keep their rows, CMEX control characters are ink, stretched
delimiters stay in one region, words kept inside a displayed formula are declared as
formula conditions, and an echoed fragment box reproduces its asset unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf as fitz
import pytest

from littrans.evidence import fold_regex_pattern, fold_term_text, term_matches
from littrans.fidelity import prepare_source
from littrans.models import AssetRef, ProjectConfig, SourceUnit, UnitKind
from littrans.storage import initialize_project_dirs, read_jsonl, save_project, sha256_file


class _Page:
    """A page without an SVG layer: regions are built from glyph metrics alone."""

    def get_text(self, mode: str) -> dict:
        return {"blocks": []}

    def get_image_info(self) -> list:
        return []

    def get_drawings(self) -> list:
        return []


def _glyph(gid: str, text: str, font: str, x: float, y: float = 100.0, width: float = 6.0,
           line: str = "b1-l0", size: float = 10.0) -> dict:
    return {"id": gid, "text": text, "font": font, "bbox": [x, y, x + width, y + size], "line": line,
            "size": size, "origin": [x, y + size * 0.8], "baseline": y + size * 0.8}


def _line(spec: list[tuple[str, str]], start: float = 100.0, y: float = 100.0, line: str = "b1-l0",
          prefix: str = "g") -> list[dict]:
    """Glyphs of one native line from (text, font) pairs; an empty text is a TeX gap."""
    glyphs, x = [], start
    for index, (text, font) in enumerate(spec):
        if text:
            glyphs.append(_glyph(f"{prefix}{index}", text, font, x, y, line=line))
        x += 6.0
    return glyphs


@pytest.mark.parametrize("pattern, folded", [
    (r"\bH¨older\b", r"\bholder\b"),
    ("Chebyshev’s", "chebyshev's"),
    (r"\Bolder\S+", r"\Bolder\S+"),
    (r"Lévy\s+process", r"levy\s+process"),
])
def test_regex_terms_fold_their_literals_but_keep_escapes(pattern: str, folded: str) -> None:
    assert fold_regex_pattern(pattern) == folded
    source = fold_term_text("the H¨older inequality; L´evy process; Chebyshev’s inequality; solder.")
    assert term_matches({"source": pattern, "match": "regex"}, source)
    assert term_matches({"source": "Hölder", "match": "regex"}, source)
    assert not term_matches({"source": r"\bolder\b", "match": "regex"}, source)


def test_native_text_equation_units_render_as_text() -> None:
    from littrans.evidence import equation_is_notation, equation_markdown
    from littrans.rendering import _target_markdown, _unit_html

    unit = SourceUnit(unit_id="p0027-b5", page=27, kind=UnitKind.EQUATION, bbox=(0, 0, 10, 10), source_text="Prob",
                      source_markdown="Prob", source_hash="h", confidence=0)
    assert not equation_is_notation(unit)
    assert "<mi>" not in _unit_html(unit, None, source_view=True)
    assert _unit_html(unit, "概率", source_view=False) == '<div class="fidelity-complex display-line">概率</div>'
    assert _target_markdown(unit, "概率") == "概率"
    assert equation_markdown(unit) == "Prob"
    notation = unit.model_copy(update={"source_text": "x = y^2", "equation_number": "1.6"})
    assert equation_is_notation(notation)
    assert _unit_html(notation, None, source_view=True).startswith('<div class="math display">')
    assert equation_markdown(notation) == "$$\nx = y^2 \\tag{1.6}\n$$"
    assert equation_is_notation(unit.model_copy(update={"latex": r"\operatorname{Prob}"}))


def test_display_rows_render_as_a_column_with_a_lead_asset() -> None:
    from littrans.rendering import _target_markdown, _unit_html

    text = ("{{asset:a-p0026-brace}} {{asset:a-p0026-x1}}, if the coin is a head,\n"
            "{{asset:a-p0026-x0}}, if the coin is a tail.")
    unit = SourceUnit(unit_id="p0026-b4", page=26, kind=UnitKind.EQUATION, bbox=(0, 0, 100, 40), source_text=text,
                      source_markdown=text, source_hash="h", confidence=0, asset_refs=[
                          AssetRef(kind="fidelity", path="brace.png", bbox=(0, 0, 10, 40)),
                          AssetRef(kind="fidelity", path="x1.png", bbox=(15, 2, 25, 12)),
                          AssetRef(kind="fidelity", path="x0.png", bbox=(15, 25, 25, 35)),
                      ])
    target = text.replace("if the coin is a head", "若为正面").replace("if the coin is a tail", "若为反面")
    for translation, source_view in ((None, True), (target, False)):
        rendered = _unit_html(unit, translation, source_view=source_view)
        assert rendered.count('<span class="display-row">') == 2
        assert rendered.startswith('<div class="fidelity-complex display-line multirow">'
                                   '<span class="display-lead">{{asset:a-p0026-brace}}</span>')
    # Ambiguous/missing fragment geometry must leave the whole first row intact.
    for refs in ([], unit.asset_refs[:-1], [*unit.asset_refs, unit.asset_refs[-1]]):
        rendered = _unit_html(unit.model_copy(update={"asset_refs": refs}), None, source_view=True)
        assert 'class="display-lead"' not in rendered
        assert '<span class="display-row">' + text.split("\n")[0] + '</span>' in rendered
    for last_box in ((15, 25, 25, 45), (5, 25, 25, 35)):
        refs = [*unit.asset_refs[:-1], unit.asset_refs[-1].model_copy(update={"bbox": last_box})]
        assert 'class="display-lead"' not in _unit_html(unit.model_copy(update={"asset_refs": refs}), None, source_view=True)
    # A row without an identifiable formula cannot establish a spanning lead.
    assert 'class="display-lead"' not in _unit_html(unit, target.replace("{{asset:a-p0026-x0}}", "0"), source_view=False)
    assert _target_markdown(unit, None).count("  \n") == 1


def test_row_breaks_are_not_coalesced_into_one_inline_asset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.fidelity as fidelity
    from littrans.fidelity import _hash, _make_unit
    from littrans.fidelity_models import load_assets
    from littrans.source_structure import coalesce_inline_assets

    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {"status": "unavailable", "reason": "isolated test", "pages": {}})
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((60, 80), "Let x = 1 and y = 2.")
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="t", title="t", source_path="source/book.pdf", source_sha256=sha256_file(pdf), source_pages=1, profile="technical-book"))
    prepare_source(tmp_path, allow_missing_layout=True)
    assets = load_assets(tmp_path)
    ids = [aid for aid, a in assets.items() if a.kind == "math" and not a.display]
    assert len(ids) >= 2
    rows = _make_unit(1, "p1-b1", "{{asset:" + ids[0] + "}}\n{{asset:" + ids[1] + "}}", [0, 0, 10, 10], assets, kind="equation")
    same_row = _make_unit(1, "p1-b2", "{{asset:" + ids[0] + "}} {{asset:" + ids[1] + "}}", [0, 0, 10, 10], assets, kind="equation")
    kept = coalesce_inline_assets([rows], dict(assets), _make_unit, _hash)[0]
    assert kept.source_text == rows.source_text
    merged = coalesce_inline_assets([same_row], dict(assets), _make_unit, _hash)[0]
    assert len(re.findall(r"\{\{asset:", merged.source_text)) == 1


def test_prepared_display_block_keeps_its_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.fidelity as fidelity

    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 80), "Consider a fair coin and define the outcome.")
        page.insert_text((60, 94), "Then the following holds for every trial.")
        page.insert_text((160, 120), "X = 1, if the coin shows a head,")
        page.insert_text((160, 134), "X = 0, if the coin shows a tail.")
        doc.save(pdf)
    box = {"label": "display_formula", "bbox": [300, 214, 600, 296]}
    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {
        "status": "ok", "fingerprint": "t", "pages": {str(Path(p).resolve()): [box] for p in images}})
    save_project(tmp_path, ProjectConfig(project_id="t", title="t", source_path="source/book.pdf", source_sha256=sha256_file(pdf), source_pages=1, profile="technical-book"))
    prepare_source(tmp_path)
    units = read_jsonl(tmp_path / "derived/units.jsonl", SourceUnit)
    display = [u for u in units if u.kind is UnitKind.EQUATION]
    assert len(display) == 1
    rows = display[0].source_text.split("\n")
    assert len(rows) == 2 and rows[0].endswith("head,") and rows[1].endswith("tail.")
    assert display[0].translatable
    from littrans.rendering import _unit_html

    target = display[0].source_text.replace("if the coin shows a head", "若为正面").replace("if the coin shows a tail", "若为反面")
    for translation, source_view in ((None, True), (target, False)):
        rendered = _unit_html(display[0], translation, source_view=source_view)
        assert 'class="display-lead"' not in rendered
        expected_rows = (translation or display[0].source_text).split("\n")
        assert re.findall(r'<span class="display-row">(.*?)</span>', rendered) == expected_rows


def test_cmex_control_characters_are_ink_and_owned_by_the_display() -> None:
    from littrans.fidelity import _boundary_diagnostics, _regions
    from littrans.source_structure import inked_glyph

    assert inked_glyph({"text": "\r", "font": "CMEX10"})
    assert inked_glyph({"text": "\n", "font": "CMEX10"})
    assert not inked_glyph({"text": "\r", "font": "CMR10"})
    assert not inked_glyph({"text": "\t", "font": "SFRM1000"})
    line = _line([("\r", "CMEX10"), ("f", "CMMI10"), ("(", "CMR10"), ("x", "CMMI10"), (")", "CMR10"), ("d", "CMMI10"), ("x", "CMMI10")])
    regions = _regions(_Page(), line, [{"label": "display_formula", "bbox": [190, 190, 300, 230]}])
    assert len(regions) == 1 and regions[0]["display"]
    assert set(regions[0]["glyph_ids"]) == {g["id"] for g in line}
    # The packet diagnostic names the hole an integral owned elsewhere would leave.
    assets = [{"id": "display", "kind": "math", "display": True,
               "fragments": [{"bbox": [100, 100, 142, 110], "glyph_ids": [g["id"] for g in line[1:]]}]},
              {"id": "integral", "kind": "math", "display": False,
               "fragments": [{"bbox": [100, 100, 106, 110], "glyph_ids": [line[0]["id"]]}]}]
    codes = {d["code"]: d for d in _boundary_diagnostics(line, assets)}
    assert codes["math-ink-outside-ownership"]["asset_id"] == "display"
    assert codes["math-ink-outside-ownership"]["glyph_ids"] == [line[0]["id"]]


def test_display_words_become_formula_conditions() -> None:
    from littrans.fidelity import _regions
    from littrans.fidelity_models import FidelityAsset

    # "1, if x ∈ [0,1],"  with TeX word gaps (no space glyph) around "if".
    cases = _line([("1", "CMR10"), (",", "CMR10"), ("", ""), ("i", "SFRM1000"), ("f", "SFRM1000"), ("", ""), ("x", "CMMI10"), ("", ""),
                   ("∈", "CMSY10"), ("", ""), ("[", "CMR10"), ("0", "CMR10"), (",", "CMR10"), ("1", "CMR10"), ("]", "CMR10"), (",", "CMR10")])
    # "x2+1, if x is even,"  keeps its words: no gap sets the phrase off from the notation.
    even = _line([("x", "CMMI10"), ("2", "CMR8"), ("+", "CMR10"), ("1", "CMR10"), (",", "CMR10"), ("", ""), ("i", "SFRM1000"), ("f", "SFRM1000"), ("", ""), ("x", "CMMI10"), ("", ""),
                  ("i", "SFRM1000"), ("s", "SFRM1000"), ("", ""), *[(c, "SFRM1000") for c in "even,"]], y=114, line="b1-l1", prefix="h")
    # A capitalized operator name applied to a parenthesis is notation, not language.
    operator = _line([*[(c, "CMR10") for c in "Prob"], ("(", "CMR10"), ("X", "CMMI10"), (")", "CMR10"), ("", ""), ("=", "CMR10"), ("", ""), ("p", "CMMI10")],
                     y=128, line="b1-l2", prefix="o")
    # Prose lines at the margin: the cases rows are set off from it, as in a book.
    prose = [g for row in range(4) for g in
             _line([(c, "SFRM1000") for c in "Let the coin decide"], start=40, y=20 + 12 * row, line=f"b0-l{row}", prefix=f"q{row}-")]
    glyphs = prose + cases + even + operator
    regions = _regions(_Page(), glyphs, [{"label": "display_formula", "bbox": [190, 190, 460, 280]}])
    display = [r for r in regions if r["display"]]
    assert len(display) == 1
    assert [c["source_text"] for c in display[0]["formula_conditions"]] == ["if", "if", "is even,"]
    assert "auto-formula-conditions" in display[0]["provenance"]
    declared = {gid for c in display[0]["formula_conditions"] for gid in c["glyph_ids"]}
    assert declared <= set(display[0]["glyph_ids"])
    FidelityAsset.model_validate({
        "id": "a", "kind": "math", "source_sha256": "0" * 64, "content_sha256": "1" * 64, "display": True,
        "fragments": [{"page": 1, "bbox": [0, 0, 1, 1], "png_path": "p", "svg_path": "s", "pdf_path": "d", "width": 1, "height": 1,
                       "glyph_ids": display[0]["glyph_ids"], "file_sha256": {"p": "a" * 64, "s": "b" * 64, "d": "c" * 64}}],
        "formula_conditions": display[0]["formula_conditions"]})


def test_word_gaps_become_spaces_in_declared_conditions() -> None:
    from littrans.fidelity import _formula_condition_glyphs, _spaced_text

    words = _line([("f", "SFRM1000"), ("o", "SFRM1000"), ("r", "SFRM1000"), ("", ""), ("a", "SFRM1000"), ("n", "SFRM1000"), ("y", "SFRM1000")])
    assert _spaced_text(words) == "for any"
    asset = {"kind": "math", "display": True, "fragments": [{"glyph_ids": [g["id"] for g in words]}],
             "formula_conditions": [{"glyph_ids": [g["id"] for g in words], "source_text": "for any"}]}
    assert _formula_condition_glyphs(asset, words) == {g["id"] for g in words}


def test_stretched_delimiter_pieces_stay_in_one_region() -> None:
    from littrans.fidelity import _regions

    head = _line([("f", "CMMI10"), ("", ""), ("=", "CMR10"), ("", ""), ("⎧", "CMEX10")])
    pieces = [_glyph(f"p{i}", text, "CMEX10", head[-1]["bbox"][0], 100 + 10 * i, line=f"b1-l{i}")
              for i, text in enumerate(["⎪", "⎨", "⎪", "⎩"], start=1)]
    rows = (_line([("a", "CMMI10")], start=130, y=110, line="b1-l1", prefix="r")
            + _line([("b", "CMMI10")], start=130, y=130, line="b1-l3", prefix="s"))
    glyphs = head + pieces + rows
    regions = _regions(_Page(), glyphs, [{"label": "display_formula", "bbox": [190, 190, 260, 222]}])
    merged = [r for r in regions if "stretched-delimiter-merged" in r["provenance"]]
    assert len(merged) == 1 and merged[0]["display"]
    assert {g["id"] for g in head + pieces} <= set(merged[0]["glyph_ids"])
    piece_ids = {g["id"] for g in pieces}
    assert all(len(set(r["glyph_ids"]) & piece_ids) in (0, len(piece_ids)) for r in regions)


def test_region_override_reproduces_exported_fragment_boxes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.fidelity as fidelity
    from littrans.fidelity import build_source_review_packet, import_source_review
    from littrans.fidelity_models import load_assets
    from littrans.storage import read_json, write_json

    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {"status": "unavailable", "reason": "isolated test", "pages": {}})
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((60, 80), "Let x = 1 and y = 2. Then f(x) = 3 holds.")
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="t", title="t", source_path="source/book.pdf", source_sha256=sha256_file(pdf), source_pages=1, profile="technical-book"))
    prepare_source(tmp_path, allow_missing_layout=True)
    before = {aid: a.model_dump(mode="json") for aid, a in load_assets(tmp_path).items()}
    assert before
    packet = build_source_review_packet(tmp_path, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "echo"
    regions = []
    for asset in before.values():
        fragment = asset["fragments"][0]
        region = {"id": asset["id"], "kind": asset["kind"], "display": asset["display"], "grouping_pending": asset["grouping_pending"],
                  "provenance": asset["provenance"], "bbox": list(fragment["bbox"])}
        if fragment["export_method"] != "raw-region":
            region["glyph_ids"] = fragment["glyph_ids"]
        regions.append(region)
    review["pages"][0]["override"] = {"regions": regions}
    write_json(tmp_path / "echo.json", review)
    assert import_source_review(tmp_path, tmp_path / "echo.json", confirm_visual_review=True)["changed_pages"] == [1]
    after = {aid: a.model_dump(mode="json") for aid, a in load_assets(tmp_path).items()}
    assert after.keys() == before.keys()
    for aid, asset in before.items():
        for key in ("bbox", "width", "height", "baseline", "glyph_ids"):
            assert after[aid]["fragments"][0][key] == asset["fragments"][0][key], (aid, key)
        assert after[aid]["content_sha256"] == asset["content_sha256"]


def test_source_checkpoint_groups_pending_assets_per_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.fidelity as fidelity
    from littrans.source_render import render_source_review

    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {"status": "unavailable", "reason": "isolated test", "pages": {}})
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 80), "Let x = 1. Then y = 2 and z = 3 hold.")
        for y in (100, 130, 160):
            page.draw_line((100, y), (150, y + 20))
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="t", title="t", source_path="source/book.pdf", source_sha256=sha256_file(pdf), source_pages=1, profile="technical-book"))
    prepare_source(tmp_path, allow_missing_layout=True)
    html = Path(render_source_review(tmp_path, "1")["html"]).read_text(encoding="utf-8")
    assert html.count("pending grouping decision") == 1
    assert "asset(s) with a pending grouping decision <details>" in html
