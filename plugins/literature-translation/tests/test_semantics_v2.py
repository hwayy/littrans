from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from littrans.extractor import extract_source
from littrans.models import SourceUnit, TableData, UnitKind
from littrans.project import initialize_project
from littrans.rendering import _coalesce_code_units
from littrans.semantics import (
    escape_markdown_prose,
    fenced_code,
    normalize_prose,
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
