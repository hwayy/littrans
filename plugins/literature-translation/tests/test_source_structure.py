import fitz

from littrans.fidelity import _make_unit, _native
from littrans.rendering import _inline_html, _unit_html
from littrans.source_structure import assemble_structure, plan_structure, styled_text


def test_bold_and_italic_have_valid_markdown_whitespace():
    text = styled_text([("The ", ""), (" operator norm", "**"), (" holds ", ""), ("always", "***")])
    assert text == "The  **operator norm** holds ***always***"
    rendered = _inline_html(text)
    assert "<strong>operator norm</strong>" in rendered
    assert "<strong><em>always</em></strong>" in rendered


def test_native_block_can_contain_two_indented_paragraphs():
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), "continued text")
        page.insert_text((68, 92), "A new paragraph")
        glyphs, blocks = _native(page)
        # Force the native extractor's possible two-paragraph block topology.
        combined = [
            {
                "id": "b0",
                "bbox": [50, 68, 180, 94],
                "lines": [line for b in blocks for line in b["lines"]],
            }
        ]
        plan = plan_structure(glyphs, combined, [], page.rect.height)
        assert len(plan["blocks"]) == 2


def test_paragraph_group_survives_display_equation():
    from littrans.fidelity_models import FidelityAsset, FidelityFragment

    asset = FidelityAsset(
        id="a1",
        kind="math",
        source_sha256="a" * 64,
        content_sha256="b" * 64,
        display=True,
        fragments=[
            FidelityFragment(
                page=1,
                bbox=(80, 90, 130, 100),
                width=50,
                height=10,
                png_path="x.png",
                svg_path="x.svg",
                pdf_path="x.pdf",
                file_sha256={name: "c" * 64 for name in ("x.png", "x.svg", "x.pdf")},
            )
        ],
    )
    assets = {"a1": asset}
    units = [
        _make_unit(1, "p0001-b1", "An introduction", [68, 70, 200, 80], assets),
        _make_unit(
            1, "p0001-b2", "{{asset:a1}}", [80, 90, 130, 100], assets, equation_number="2.1"
        ),
        _make_unit(1, "p0001-b3", "A continuation.", [50, 110, 200, 120], assets),
        _make_unit(1, "p0001-b4", "Another paragraph.", [68, 130, 200, 140], assets),
    ]
    plan = {
        "omitted": {},
        "notes": {},
        "note_top": 999,
        "margin": 50,
        "font_size": 10,
        "first_x": {"b1": 68, "b3": 50, "b4": 68},
    }
    result = assemble_structure(units, assets, plan, _make_unit)
    assert [u.parent_id for u in result] == ["p0001-b1"] * 3 + ["p0001-b4"]
    assert result[1].equation_number == "2.1"


def test_footnote_fragments_merge_and_strip_label_from_body():
    assets = {}
    units = [
        _make_unit(
            1, "p0001-b1", "Main text.[^1]", [50, 50, 200, 60], assets, footnote_refs=["p0001-b2"]
        ),
        _make_unit(1, "p0001-b2", "Note begins", [68, 500, 200, 510], assets),
        _make_unit(1, "p0001-b3", "and continues.", [50, 512, 200, 522], assets),
    ]
    plan = {
        "omitted": {},
        "notes": {"b2": {"number": "1"}},
        "note_top": 500,
        "margin": 50,
        "font_size": 10,
        "first_x": {"b1": 68, "b2": 68, "b3": 50},
    }
    result = assemble_structure(units, assets, plan, _make_unit)
    assert len(result) == 2
    assert result[1].source_text == "Note begins and continues."
    assert result[1].footnote_number == "1"
    assert result[0].footnote_refs == [result[1].unit_id]
    caller = _unit_html(result[0], None, source_view=True)
    note = _unit_html(result[1], None, source_view=True)
    assert 'href="#fn-p1-source-1"' in caller
    assert 'id="fn-p1-source-1"' in note


def test_header_is_omitted_when_only_part_of_block_overlaps_label():
    glyphs = [
        {
            "id": "1",
            "text": "1",
            "size": 7,
            "origin": [50, 60],
            "bbox": [50, 53, 54, 61],
            "font": "Helvetica",
            "baseline": 60,
        },
        {
            "id": "h",
            "text": "Heading",
            "size": 7,
            "origin": [150, 60],
            "bbox": [150, 53, 210, 61],
            "font": "Helvetica",
            "baseline": 60,
        },
    ]
    plan = plan_structure(
        glyphs,
        [{"id": "b0", "bbox": [50, 53, 210, 61], "lines": [["1"], ["h"]]}],
        [{"label": "header", "bbox": [295, 100, 430, 125]}],
        700,
    )
    assert plan["omitted"]["b0"] == "running-header-or-footer"


def test_partial_paragraph_selection_is_rejected_before_approval(tmp_path):
    import pytest

    from littrans.batching import create_batches
    from littrans.models import ProjectConfig
    from littrans.storage import initialize_project_dirs, save_project, write_jsonl

    initialize_project_dirs(tmp_path)
    save_project(
        tmp_path,
        ProjectConfig(
            project_id="structure",
            title="Structure",
            source_path="source.pdf",
            source_sha256="a" * 64,
            source_pages=1,
            profile="technical-book",
        ),
    )
    units = [
        _make_unit(1, "p0001-b1", "First.", [50, 50, 200, 60], {}, parent_id="p0001-b1"),
        _make_unit(1, "p0001-b2", "Second.", [50, 70, 200, 80], {}, parent_id="p0001-b1"),
    ]
    write_jsonl(tmp_path / "derived/units.jsonl", units)
    with pytest.raises(ValueError, match="logical paragraph"):
        create_batches(tmp_path, "1", unit_ids=["p0001-b1"])


def test_inline_components_keep_order_and_all_original_fragments():
    from littrans.fidelity import _hash
    from littrans.fidelity_models import FidelityAsset, FidelityFragment, asset_reference_ids
    from littrans.source_structure import coalesce_inline_assets

    assets = {}
    for i in (1, 2):
        f = FidelityFragment(
            page=1,
            bbox=(i * 20, 50, i * 20 + 10, 60),
            width=10,
            height=10,
            png_path=f"{i}.png",
            svg_path=f"{i}.svg",
            pdf_path=f"{i}.pdf",
            glyph_ids=[f"g{i}"],
            file_sha256={f"{i}.{ext}": "c" * 64 for ext in ("png", "svg", "pdf")},
        )
        assets[f"a{i}"] = FidelityAsset(
            id=f"a{i}",
            kind="math",
            source_sha256="a" * 64,
            content_sha256="b" * 64,
            fragments=[f],
            display=False,
        )
    u = _make_unit(1, "p0001-b1", "With {{asset:a1}} {{asset:a2}}.", [20, 50, 100, 60], assets)
    result = coalesce_inline_assets([u], assets, _make_unit, _hash)
    ids = asset_reference_ids(result[0].source_text)
    assert len(ids) == 1 and len(assets) == 1
    assert [f.glyph_ids for f in assets[ids[0]].fragments] == [["g1"], ["g2"]]


def test_enumerated_theorem_keeps_children_and_stops_at_proof():
    assets = {}
    texts = [
        "Theorem 12.11. For each function:",
        "(a) First condition.",
        "(b) Second condition.",
        "Proof. A new argument.",
        "Next paragraph.",
    ]
    units = [
        _make_unit(1, f"p0001-b{i}", text, [68, 50 + i * 20, 220, 60 + i * 20], assets)
        for i, text in enumerate(texts, 1)
    ]
    plan = {
        "omitted": {},
        "notes": {},
        "note_top": 999,
        "margin": 50,
        "font_size": 10,
        "first_x": {f"b{i}": 68 for i in range(1, 6)},
    }
    result = assemble_structure(units, assets, plan, _make_unit)
    assert len(result) == 5  # No clause or proof is folded into the lead-in.
    assert [u.parent_id for u in result] == ["p0001-b1"] * 3 + ["p0001-b4", "p0001-b5"]


def test_parent_rows_keep_clause_markup_and_child_anchors():
    from littrans.rendering import _group_parent_rows

    units = [
        _make_unit(
            1, f"p0001-b{i}", text, [50, 50 + i * 20, 200, 60 + i * 20], {}, parent_id="p0001-b1"
        )
        for i, text in enumerate(["Theorem.", "(a) First.", "(b) Second."], 1)
    ]
    rows = [
        {
            "unit": u,
            "source_html": f"<p>{u.source_text}</p>",
            "target_html": f"<p>译文{i}</p>",
            "assets": [],
            "reader_notes": [],
            "last_page": 1,
        }
        for i, u in enumerate(units)
    ]
    grouped = _group_parent_rows(rows)
    assert len(grouped) == 1
    assert grouped[0]["source_html"].count("<p>") == 3
    assert 'id="p0001-b2"' in grouped[0]["source_html"]
    assert 'id="p0001-b3"' in grouped[0]["source_html"]
    assert grouped[0]["target_html"].count("<p>") == 3


def test_parent_dependency_crosses_page_and_batch_scope(tmp_path):
    from littrans.evidence import dependency_closure, page_evidence_units

    units = [
        _make_unit(page, f"p{page:04d}-b1", "Clause.", [50, 50, 200, 60], {}, parent_id="p0001-b1")
        for page in (1, 2, 3)
    ]
    assert dependency_closure(tmp_path, [], ["p0002-b1"], all_units=units) == [
        u.unit_id for u in units
    ]
    assert [u.unit_id for u in page_evidence_units(2, units)] == [u.unit_id for u in units]


def test_continuation_skips_omitted_headers_but_never_missing_pages(tmp_path):
    from littrans.evidence import dependency_closure
    from littrans.models import RenderPolicy

    left = _make_unit(1, "p0001-b1", "Continued", [50, 50, 200, 60], {}, continued_to_next=True)
    header = _make_unit(
        2,
        "p0002-b0",
        "Header",
        [50, 10, 200, 20],
        {},
        render_policy=RenderPolicy.OMIT,
        translatable=False,
    )
    right = _make_unit(2, "p0002-b1", "body.", [50, 50, 200, 60], {})
    distant = _make_unit(
        5, "p0005-b1", "Different page.", [50, 50, 200, 60], {}, continues_from_previous=True
    )
    assert dependency_closure(
        tmp_path, [], [left.unit_id], all_units=[left, header, right, distant]
    ) == [left.unit_id, right.unit_id]
    assert dependency_closure(
        tmp_path, [], [distant.unit_id], all_units=[left, header, right, distant]
    ) == [distant.unit_id]


def test_batch_budget_cannot_split_theorem(tmp_path, monkeypatch):
    from littrans.batching import create_batches
    from littrans.models import ProjectConfig
    from littrans.storage import initialize_project_dirs, save_project, write_jsonl

    initialize_project_dirs(tmp_path)
    save_project(
        tmp_path,
        ProjectConfig(
            project_id="statement",
            title="Statement",
            source_path="source.pdf",
            source_sha256="a" * 64,
            source_pages=1,
            profile="technical-book",
        ),
    )
    units = [
        _make_unit(
            1,
            f"p0001-b{i}",
            "word " * 90,
            [50, i * 20, 200, i * 20 + 10],
            {},
            parent_id="p0001-b1" if i < 4 else "p0001-b4",
        )
        for i in range(1, 5)
    ]
    write_jsonl(tmp_path / "derived/units.jsonl", units)
    monkeypatch.setattr("littrans.batching.require_verified_extraction", lambda *args: None)
    monkeypatch.setattr("littrans.batching._context_text", lambda *args: "")
    batches = create_batches(tmp_path, "1", max_words=100)
    assert [b.unit_ids for b in batches] == [[u.unit_id for u in units[:3]], [units[3].unit_id]]


def test_formula_condition_declaration_cannot_hide_multiple_lines_or_wrong_text():
    import pytest

    from littrans.fidelity import _formula_condition_glyphs, _opaque_prose_assets

    glyphs = [
        {
            "id": str(i),
            "text": word,
            "baseline": 50 if i < 3 else 80,
            "size": 10,
            "font": "Helvetica",
        }
        for i, word in enumerate(
            [
                "and ",
                "is ",
                "odd ",
                "recoverable ",
                "neighboring ",
                "paragraph ",
                "contains ",
                "many ",
                "words ",
            ]
        )
    ]
    asset = {
        "id": "cases",
        "kind": "math",
        "display": True,
        "fragments": [{"glyph_ids": [g["id"] for g in glyphs]}],
        "formula_conditions": [{"glyph_ids": ["0", "1", "2"], "source_text": "and is odd "}],
    }
    assert _opaque_prose_assets({"ledger": {"glyphs": glyphs}, "assets": [asset]}) == ["cases"]
    asset["formula_conditions"] = [{"glyph_ids": ["0", "3"], "source_text": "and recoverable "}]
    with pytest.raises(ValueError, match="visual line"):
        _formula_condition_glyphs(asset, glyphs)
    asset["formula_conditions"] = [{"glyph_ids": ["0"], "source_text": "different"}]
    with pytest.raises(ValueError, match="exactly match"):
        _formula_condition_glyphs(asset, glyphs)
