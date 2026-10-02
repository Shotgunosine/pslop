import pytest

from conftest import REPO
from pmap_calibrate.loaders import load_text, plain_text


def test_markdown_unescapes_and_plain_text_strips_markup(tmp_path):
    path = tmp_path / "n.md"
    path.write_text("## **Heading**\n\n* Did it \\[OV\\]\n  * Sub-item\n", encoding="utf-8")
    text = load_text(path)
    assert "[OV]" in text and "\\[" not in text
    assert plain_text(text, path) == "Heading\n\nDid it [OV]\nSub-item\n"


def test_docx_paragraphs_and_tables_in_order(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("First paragraph")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Element"
    table.cell(0, 1).text = "Level 5"
    document.add_paragraph("Last paragraph")
    path = tmp_path / "n.docx"
    document.save(path)
    assert load_text(path).splitlines() == ["First paragraph", "Element | Level 5", "Last paragraph"]


def test_pdf():
    pdf = REPO / "HHS Closeout Email for Covered Non-SES and Equivalent Employees.pdf"
    if not pdf.exists():
        pytest.skip("HHS guidance PDF not present")
    text = load_text(pdf)
    assert "Organizational Value" in text
    assert "6000 characters" in text


def test_unsupported_and_empty(tmp_path):
    with pytest.raises(ValueError, match="Unsupported"):
        load_text(tmp_path / "n.rtf")
    empty = tmp_path / "empty.txt"
    empty.write_text("  \n", encoding="utf-8")
    with pytest.raises(ValueError, match="No text"):
        load_text(empty)
