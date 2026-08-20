from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from littrans.extractor import _fonts_are_monospace, extract_source
from littrans.models import SourceUnit, TableData, UnitKind
from littrans.project import initialize_project
from littrans.rendering import (
    _coalesce_code_units,
    _coalesce_table_units,
    _continuation_separator,
    _continued_note_markdown,
    _inline_html,
    _merge_continued_note_html,
)
from littrans.semantics import (
    code_from_block,
    detect_code_language,
    escape_markdown_prose,
    fenced_code,
    looks_like_continuation,
    looks_like_program_code,
    normalize_prose,
    prose_from_block,
    split_glued_listing,
    split_mixed_pdf_block,
    table_to_html,
    table_to_markdown,
)


def make_layout_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(72, height - 60, "Two-column sample")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(72, height - 100, "Left column first paragraph.")
    pdf.drawString(72, height - 125, "Left column second paragraph.")
    pdf.drawString(width / 2 + 24, height - 100, "Right column first paragraph.")
    pdf.drawString(width / 2 + 24, height - 125, "Right column second paragraph.")
    pdf.drawString(72, 70, "This sentence continues across the")
    pdf.showPage()
    pdf.setFont("Helvetica", 10)
    pdf.drawString(72, height - 35, "page boundary without a new paragraph.")
    pdf.drawString(72, height - 105, "The relation a = b + 3 is used here.")
    pdf.drawString(72, height - 125, "The identity x^2 + y^2 = r^2 defines a circle.")
    pdf.setFont("Courier", 9)
    pdf.drawString(72, height - 145, '<Grid Name="Root">')
    pdf.drawString(72, height - 158, "  <Button />")
    pdf.drawString(72, height - 171, "</Grid>")
    pdf.showPage()
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(72, height - 60, "Note Keep both lines in one callout.")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(72, height - 74, "The second line must not become ordinary prose.")
    for offset in range(5):
        pdf.line(120 + offset * 70, 260, 120 + offset * 70, 430)
    for offset in range(4):
        pdf.line(120, 260 + offset * 55, 400, 260 + offset * 55)
    pdf.drawString(130, 405, "Model A")
    pdf.drawString(72, 235, "Figure 1. Vector chart with one legend label.")
    pdf.save()


def test_two_column_order_continuation_inline_math_and_code(tmp_path: Path) -> None:
    source = tmp_path / "layout.pdf"
    root = tmp_path / "project"
    make_layout_pdf(source)
    initialize_project(source, root, "technical-book")
    units = extract_source(root, "1-3")
    text = [unit.source_text for unit in units]
    assert text.index("Left column second paragraph.") < text.index(
        "Right column first paragraph."
    )
    continuation = next(unit for unit in units if unit.source_text.startswith("page boundary"))
    assert continuation.continues_from_previous
    formula_prose = next(unit for unit in units if "relation" in unit.source_text)
    assert formula_prose.source_markdown and "$a = b + 3$" in formula_prose.source_markdown
    polynomial = next(unit for unit in units if "identity" in unit.source_text)
    assert polynomial.source_markdown and "$x^2 + y^2 = r^2$" in polynomial.source_markdown
    code = next(unit for unit in units if unit.kind == "code")
    assert code.code_language == "xaml"
    assert "\n  <Button />\n" in code.source_text
    note = next(unit for unit in units if unit.kind == "note")
    assert "second line" in note.source_text
    page_3 = [unit for unit in units if unit.page == 3]
    assert not any(unit.kind == "table" for unit in page_3)
    assert any(unit.kind == "figure" and "Model A" in unit.source_text for unit in page_3)


def test_local_table_code_and_literal_tag_markdown() -> None:
    table = TableData(
        rows=[["Name", "Description"], ["Button", "A <Button> element"]],
        header_rows=1,
        column_count=2,
    )
    rendered = table_to_markdown(table)
    assert "| Name | Description |" in rendered
    assert "&lt;Button&gt;" in rendered
    code = fenced_code("<Button>\n  content\n</Button>", "xml")
    assert code.startswith("```xml\n<Button>")
    assert escape_markdown_prose("Use <Button> here") == "Use &lt;Button&gt; here"


def test_inline_html_renders_code_spans_and_math_without_allowing_raw_html() -> None:
    rendered = _inline_html(
        "Use `<Button>` with $a = b + 3$, `` `literal` ``, and `F` *value*."
    )
    assert "<code>&lt;Button&gt;</code>" in rendered
    assert '<span class="math inline">' in rendered
    assert "<code>`literal`</code>" in rendered
    assert "<code>F</code> <em>value</em>" in rendered
    assert "<Button>" not in rendered


def test_inline_html_renders_code_and_math_nested_inside_emphasis() -> None:
    rendered = _inline_html("*设置元素的 `Opacity` 属性和 $x$ 值：*")
    assert rendered.startswith("<em>")
    assert "<code>Opacity</code>" in rendered
    assert '<span class="math inline">' in rendered
    assert rendered.endswith("</em>")
    assert "`" not in rendered


def test_embedded_font_control_glyphs_are_normalized() -> None:
    assert normalize_prose("\x02 A list item") == "• A list item"
    assert normalize_prose("File \x02 New \x02 Project") == "File → New → Project"


def test_cross_page_code_fragments_keep_exact_indentation_in_one_fence() -> None:
    first = SourceUnit(
        unit_id="p0001-u001-code",
        kind=UnitKind.CODE,
        page=1,
        bbox=[1, 1, 2, 2],
        source_text='<Window\n    xmlns="urn:wpf"',
        source_hash="a" * 64,
        translatable=False,
        code_language="xaml",
        continued_to_next=True,
        confidence=1.0,
    )
    second = SourceUnit(
        unit_id="p0002-u001-code",
        kind=UnitKind.CODE,
        page=2,
        bbox=[1, 1, 2, 2],
        source_text="  <Grid>\n  </Grid>\n</Window>",
        source_hash="b" * 64,
        translatable=False,
        code_language="xaml",
        continues_from_previous=True,
        confidence=1.0,
    )
    combined, grouped = _coalesce_code_units([first, second])
    assert len(combined) == 1
    assert combined[0].source_text == (
        '<Window\n    xmlns="urn:wpf"\n  <Grid>\n  </Grid>\n</Window>'
    )
    assert grouped[first.unit_id] == [first.unit_id, second.unit_id]


def test_continued_table_half_rows_render_as_one_logical_row() -> None:
    first = SourceUnit(
        unit_id="p0001-u001-table",
        kind=UnitKind.TABLE,
        page=1,
        bbox=[1, 1, 2, 2],
        source_text="PropertyName |",
        source_hash="a" * 64,
        translatable=True,
        table=TableData(rows=[["PropertyName", ""]], header_rows=0, column_count=2),
        continued_to_next=True,
        confidence=1.0,
    )
    second = SourceUnit(
        unit_id="p0001-u002-table",
        kind=UnitKind.TABLE,
        page=1,
        bbox=[2, 1, 3, 2],
        source_text="| Property description.",
        source_hash="b" * 64,
        translatable=True,
        table=TableData(rows=[["", "Property description."]], header_rows=0, column_count=2),
        continues_from_previous=True,
        confidence=1.0,
    )
    combined, grouped = _coalesce_table_units([first, second])
    assert len(combined) == 1
    assert combined[0].table == TableData(
        rows=[["PropertyName", "Property description."]],
        header_rows=0,
        column_count=2,
    )
    assert grouped[first.unit_id] == [first.unit_id, second.unit_id]


def test_headerless_table_keeps_every_row_as_body_data() -> None:
    table = TableData(
        rows=[["First", "One"], ["Second", "Two"]],
        header_rows=0,
        column_count=2,
    )
    markdown = table_to_markdown(table)
    assert markdown.splitlines() == [
        "|  |  |",
        "| --- | --- |",
        "| First | One |",
        "| Second | Two |",
    ]
    rendered = table_to_html(table)
    assert "<thead>" not in rendered
    assert rendered.count("<tr>") == 2
    assert "<tbody><tr><td>First</td><td>One</td></tr>" in rendered


def test_continuation_separator_preserves_english_words_without_spacing_cjk() -> None:
    assert _continuation_separator("Doing so will", "give you") == " "
    assert _continuation_separator("这样做", "能让你") == ""
    assert _continuation_separator("<p>这样做</p>", "<p>能让你</p>") == ""
    assert _continuation_separator("因为即使", "`Canvas` 的大小") == " "
    assert _continuation_separator("使用 `Canvas`", "即可绘制") == " "
    assert _continuation_separator(
        "因为即使", '<a id="u2"></a><code>Canvas</code> 的大小'
    ) == " "
    assert _continuation_separator("使用 <code>Canvas</code>", "即可绘制") == " "


def test_cross_page_note_continuation_keeps_one_callout() -> None:
    rendered = "> [!TIP]\n> 下一页的提示正文。"
    assert _continued_note_markdown(rendered, '<a id="u2"></a>') == (
        '> <a id="u2"></a>下一页的提示正文。'
    )
    left = '<aside class="source-note"><strong>提示</strong><p>第一部分</p></aside>'
    right = '<aside class="source-note"><strong>提示</strong><p>第二部分</p></aside>'
    assert _merge_continued_note_html(left, right, '<a id="u2"></a>') == (
        '<aside class="source-note"><strong>提示</strong><p>'
        '第一部分<a id="u2"></a>第二部分</p></aside>'
    )


def test_flattened_csharp_listings_are_recognized_as_code() -> None:
    assert looks_like_program_code("while (nav.CanGoBack) { nav.RemoveBackEntry();}")
    assert looks_like_program_code(
        'string pageName; while (pageName != "ConfigureAppWizard.xaml") '
        "{ JournalEntry entry = nav.RemoveBackEntry(); }"
    )
    assert looks_like_program_code(
        "private void Replay(ListSelectionJournalEntry state) { lstSource.Items.Clear(); }"
    )
    assert looks_like_program_code(
        'xmlns:local="clr-namespace:NavigationApplication" '
        'x:Class="NavigationApplication.SelectProductPageFunction"'
    )
    assert looks_like_program_code("// Now perform the change. lstTarget.Items.Add(itemText);")
    assert not looks_like_program_code(
        "Unfortunately, WPF doesn't allow you to have much control over the "
        "navigation stack. It just gives you two methods: AddBackEntry() and "
        "RemoveBackEntry()."
    )
    assert not looks_like_program_code(
        "public interface for you to use in your applications."
    )
    assert not looks_like_program_code("AnnotationHelper.GetAnchorInfo() method,")
    assert not looks_like_program_code("BackgroundWorker.CancelAsync() method,")
    assert not looks_like_program_code(
        "fixed documents, 883 flow documents, 884 DoDragDrop() method, 131 "
        "DoubleAnimation class, 297, 393, 395"
    )
    assert not looks_like_program_code(
        "byte array, 608 ConvertBack() method, 610 Convert() method, 610 "
        "ImageDirectory property, 609 ImagePathConverter class, 609"
    )
    assert looks_like_program_code("this.Cursor = null;}")
    assert looks_like_program_code("this.view = view;")
    assert not looks_like_continuation(
        "update the lists, as shown here:",
        "private void Replay(ListSelectionJournalEntry state) {",
    )
    assert split_glued_listing(
        "You can handle the change as shown here: private void Replay() {"
    ) == ("You can handle the change as shown here:", "private void Replay() {")
    assert split_glued_listing(
        "For example: private void Replay() {"
    ) == ("For example:", "private void Replay() {")
    assert split_glued_listing("Use this: void Replay() {") == (
        "Use this:",
        "void Replay() {",
    )
    assert split_glued_listing(
        "// Look at paragraphs. foreach (Block block in document.Blocks) { }"
    ) is None
    assert split_glued_listing("case Ready: return Advance();") is None
    assert split_glued_listing(
        "Unfortunately, WPF doesn't allow you to have much control over the "
        "navigation stack. It just gives you two methods: AddBackEntry() and "
        "RemoveBackEntry()."
    ) is None


def test_ambiguous_dotted_calls_are_not_labeled_as_csharp() -> None:
    assert detect_code_language("console.log(1);") == "text"
    assert detect_code_language("System.out.println(1);") == "text"
    assert detect_code_language("private void Replay() { nav.GoBack(); }") == "csharp"


def test_split_mixed_pdf_block_keeps_listing_out_of_prose() -> None:
    block = {
        "type": 0,
        "bbox": (72, 700, 420, 742),
        "lines": [
            {
                "bbox": (72, 700, 420, 714),
                "spans": [{"text": "You can handle this as shown here:", "size": 10}],
            },
            {
                "bbox": (72, 716, 420, 728),
                "spans": [{"text": "private void Replay()", "size": 10}],
            },
            {
                "bbox": (72, 728, 420, 742),
                "spans": [{"text": "{", "size": 10}],
            },
        ],
    }
    parts = split_mixed_pdf_block(block)
    assert len(parts) == 2
    assert prose_from_block(parts[0]) == "You can handle this as shown here:"
    assert "private void Replay()" in code_from_block(parts[1])
    assert "shown here" not in code_from_block(parts[1])


def test_body_font_while_loop_extracts_as_code(tmp_path: Path) -> None:
    source = tmp_path / "code.pdf"
    root = tmp_path / "project"
    pdf = canvas.Canvas(str(source), pagesize=letter)
    width, height = letter
    pdf.setFont("Helvetica", 10)
    pdf.drawString(72, height - 80, "Clear the back list with this loop:")
    pdf.drawString(72, height - 110, "while (nav.CanGoBack) { nav.RemoveBackEntry(); }")
    pdf.showPage()
    pdf.setFont("Helvetica", 10)
    pdf.drawString(72, height - 40, "private void Replay()")
    pdf.drawString(72, height - 54, "{")
    pdf.drawString(90, height - 68, "lstSource.Items.Clear();")
    pdf.drawString(72, height - 82, "}")
    pdf.save()
    initialize_project(source, root, "technical-book")
    units = extract_source(root, "1-2")
    listings = [unit for unit in units if unit.kind == "code"]
    assert any("CanGoBack" in unit.source_text for unit in listings)
    assert any("Replay" in unit.source_text for unit in listings)
    replay = next(unit for unit in units if "Replay" in unit.source_text)
    assert replay.kind == "code"
    assert not replay.continues_from_previous


def test_static_constructor_stays_with_its_listing_body() -> None:
    block = {
        "bbox": (72, 700, 420, 742),
        "lines": [
            {
                "bbox": (72, 700, 420, 714),
                "spans": [{"text": "static DataCommands() {", "size": 10}],
            },
            {
                "bbox": (72, 716, 420, 728),
                "spans": [{"text": "// Initialize the command.", "size": 10}],
            },
            {
                "bbox": (72, 728, 420, 742),
                "spans": [{"text": "requery = new RoutedUICommand();", "size": 10}],
            },
        ],
    }
    parts = split_mixed_pdf_block(block)
    assert len(parts) == 1
    assert looks_like_program_code(code_from_block(parts[0]))


def test_wrapped_prose_that_looks_like_a_listing_lead_is_not_split() -> None:
    samples = [
        (
            "WPF can perform the same work",
            "using software calculations if necessary.",
        ),
        (
            "The element name maps to a class. For example, the element",
            "<Button instructs WPF to create a Button object.",
        ),
        (
            "This namespace is declared without a",
            "namespace prefix, so it becomes the default namespace.",
        ),
        (
            "You cannot substitute <button> for",
            "<Button>. However, type converters are not case-sensitive.",
        ),
    ]
    for previous, current in samples:
        block = {
            "bbox": (72, 700, 420, 728),
            "lines": [
                {
                    "bbox": (72, 700, 420, 714),
                    "spans": [{"text": previous, "size": 10}],
                },
                {
                    "bbox": (72, 716, 420, 728),
                    "spans": [{"text": current, "size": 10}],
                },
            ],
        }
        assert split_mixed_pdf_block(block) == [block]

    assert not looks_like_program_code("<Button instructs WPF to create an object.")
    assert not looks_like_program_code(
        "<Button>. However, type converters are not case-sensitive."
    )
    assert looks_like_program_code('<Button Content="Save" />')
    assert looks_like_program_code("<Grid><Button /></Grid>")
    assert not looks_like_program_code(
        "<Product> element inside the <Products> element."
    )
    assert not looks_like_program_code("<Button> and <TextBox> are controls.")


def test_ambiguous_generic_declarations_remain_language_neutral() -> None:
    assert detect_code_language("var x = 1;") == "text"
    assert detect_code_language("int main() { return 0; }") == "text"
    assert detect_code_language("foreach (var item in items) { Use(item); }") == "csharp"


def test_monospace_font_detection_does_not_match_monotype_vendor_name() -> None:
    assert _fonts_are_monospace({"TheSansMono-Plain"})
    assert _fonts_are_monospace({"CourierNewPSMT"})
    assert not _fonts_are_monospace({"Monotype Corsiva"})


def test_short_glued_prose_and_listing_split_then_merge(tmp_path: Path) -> None:
    source = tmp_path / "glued.pdf"
    root = tmp_path / "project"
    pdf = canvas.Canvas(str(source), pagesize=letter)
    width, height = letter
    del width
    pdf.setFont("Helvetica", 10)
    pdf.drawString(72, height - 80, "For example: private void Replay()")
    pdf.drawString(72, height - 94, "{")
    pdf.drawString(90, height - 108, "lstSource.Items.Clear();")
    pdf.drawString(72, height - 122, "}")
    pdf.save()
    initialize_project(source, root, "technical-book")
    units = extract_source(root, "1")
    prose = next(unit for unit in units if "For example" in unit.source_text)
    listing = next(unit for unit in units if "private void Replay" in unit.source_text)
    assert prose.kind == "paragraph"
    assert "private void" not in prose.source_text
    assert listing.kind == "code"
    assert "lstSource.Items.Clear();" in listing.source_text
    assert "For example" not in listing.source_text
