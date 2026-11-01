"""Rebuild PDF text and detected tables as editable Word content."""

import bisect
import re
import sys
from pathlib import Path

import fitz
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt


def _edges(table, axis):
    first, second = (0, 2) if axis == "x" else (1, 3)
    values = sorted(
        value
        for row in table.rows
        for cell in row.cells
        if cell
        for value in (cell[first], cell[second])
    )
    result = []
    for value in values:
        if not result or abs(value - result[-1]) > 0.8:
            result.append(value)
    return result


def _set_cell_text(cell, items):
    items.sort(key=lambda item: (item[0], item[1]))
    text = " ".join(item[2].strip() for item in items if item[2].strip())
    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = 1
    if text:
        run = paragraph.add_run(text)
        run.font.name = "Arial"
        run.font.size = Pt(6)
        run.bold = any(item[3] & 16 for item in items)
    if text and re.fullmatch(r"[\d₹.,()%+\-\s]+", text):
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    properties = cell._tc.get_or_add_tcPr()
    margins = OxmlElement("w:tcMar")
    for side, value in (("top", "0"), ("start", "25"), ("bottom", "0"), ("end", "25")):
        margin = OxmlElement(f"w:{side}")
        margin.set(qn("w:w"), value)
        margin.set(qn("w:type"), "dxa")
        margins.append(margin)
    properties.append(margins)


def _add_table(document, page, table, printable_width):
    x_edges = _edges(table, "x")
    y_edges = _edges(table, "y")
    if len(x_edges) < 2 or len(y_edges) < 2:
        return

    source_width = x_edges[-1] - x_edges[0]
    table_bounds = fitz.Rect(table.bbox)
    centered_titles = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if len(line["spans"]) != 1:
                continue
            span = line["spans"][0]
            x0, y0, x1, _y1 = span["bbox"]
            center_x = (x0 + x1) / 2
            if (
                table_bounds.y0 - 2 <= y0 <= table_bounds.y0 + 15
                and abs(center_x - (table_bounds.x0 + table_bounds.x1) / 2) <= source_width * 0.05
                and x1 - x0 <= source_width * 0.7
                and len(span["text"]) < 150
            ):
                centered_titles.append((y0, span))
    for _y, span in centered_titles:
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_before = Pt(1)
        paragraph.paragraph_format.space_after = Pt(1)
        run = paragraph.add_run(span["text"].strip())
        run.font.name = "Arial"
        run.font.size = Pt(max(7, min(11, span["size"])))
        run.bold = True

    row_count = len(y_edges) - 1
    column_count = len(x_edges) - 1
    word_table = document.add_table(rows=row_count, cols=column_count)
    word_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    word_table.autofit = False
    word_table.style = "Table Grid"
    for column_index in range(column_count):
        width = printable_width * (x_edges[column_index + 1] - x_edges[column_index]) / source_width
        for cell in word_table.columns[column_index].cells:
            cell.width = width

    cell_items = [[[] for _ in range(column_count)] for _ in range(row_count)]
    bounds = fitz.Rect(x_edges[0], y_edges[0], x_edges[-1], y_edges[-1])
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                x0, y0, x1, y1 = span["bbox"]
                if any(abs(y0 - title_y) < 0.1 and span["text"] == title_span["text"] for title_y, title_span in centered_titles):
                    continue
                center_x, center_y = (x0 + x1) / 2, (y0 + y1) / 2
                if not bounds.contains(fitz.Point(center_x, center_y)):
                    continue
                column = min(column_count - 1, max(0, bisect.bisect_right(x_edges, center_x) - 1))
                row = min(row_count - 1, max(0, bisect.bisect_right(y_edges, center_y) - 1))
                cell_items[row][column].append((y0, x0, span["text"], span["flags"], x1 - x0))

    for row_index, row in enumerate(cell_items):
        row_items = [item for items in row for item in items]
        if len(row_items) == 1 and row_items[0][4] < source_width * 0.8:
            merged = word_table.cell(row_index, 0).merge(word_table.cell(row_index, column_count - 1))
            _set_cell_text(merged, row_items)
            continue
        for column_index, items in enumerate(row):
            _set_cell_text(word_table.cell(row_index, column_index), items)


def _add_line(document, line):
    spans = sorted(line["spans"], key=lambda span: span["bbox"][0])
    if not spans:
        return
    text = " ".join(span["text"].strip() for span in spans if span["text"].strip())
    if not text:
        return
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(1)
    paragraph.paragraph_format.line_spacing = 1
    if line["bbox"][1] < 80 and len(text) < 120:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for span in spans:
        value = span["text"].strip()
        if not value:
            continue
        run = paragraph.add_run(value + " ")
        run.font.name = "Arial"
        run.font.size = Pt(max(6, min(14, span["size"] * 0.82)))
        run.bold = bool(span["flags"] & 16)


def _line_is_in_table(line, table_bounds):
    line_bounds = fitz.Rect(line["bbox"])
    if line_bounds.is_empty:
        return False
    center = fitz.Point((line_bounds.x0 + line_bounds.x1) / 2, (line_bounds.y0 + line_bounds.y1) / 2)
    for bounds in table_bounds:
        if bounds.contains(center):
            return True
        intersection = line_bounds & bounds
        if not intersection.is_empty and intersection.get_area() / max(1, line_bounds.get_area()) > 0.5:
            return True
    return False


def _append_ocr_page(document, text, page_number):
    if not text.strip():
        return
    heading = document.add_heading(f"Scanned page {page_number}", level=2)
    heading.paragraph_format.keep_with_next = True
    for line in text.splitlines():
        if line.strip():
            document.add_paragraph(line.strip())


def convert(source, destination, ocr_text_path=None):
    pdf = fitz.open(source)
    if not pdf.page_count:
        raise ValueError("The PDF contains no pages.")
    document = Document()
    section = document.sections[0]
    section.page_width = Pt(pdf[0].rect.width)
    section.page_height = Pt(pdf[0].rect.height)
    section.top_margin = Pt(24)
    section.bottom_margin = Pt(24)
    section.left_margin = Pt(26)
    section.right_margin = Pt(26)
    printable_width = section.page_width - section.left_margin - section.right_margin

    normal = document.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(7)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(1)
    normal.paragraph_format.line_spacing = 1

    ocr_pages = Path(ocr_text_path).read_text(encoding="utf-8").split("\f") if ocr_text_path else []
    for page_index, page in enumerate(pdf):
        try:
            tables = page.find_tables().tables
        except Exception:
            tables = []
        table_bounds = [fitz.Rect(table.bbox) for table in tables]
        elements = [(table.bbox[1], table.bbox[0], "table", table) for table in tables]
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                if not _line_is_in_table(line, table_bounds):
                    elements.append((line["bbox"][1], line["bbox"][0], "line", line))

        if not elements and page_index < len(ocr_pages):
            _append_ocr_page(document, ocr_pages[page_index], page_index + 1)
        else:
            elements.sort(key=lambda item: (round(item[0] / 3) * 3, item[1]))
            for _top, _left, kind, item in elements:
                if kind == "table":
                    _add_table(document, page, item, printable_width)
                else:
                    _add_line(document, item)
            if page_index < len(ocr_pages) and ocr_pages[page_index].strip():
                _append_ocr_page(document, ocr_pages[page_index], page_index + 1)

        if page_index < len(pdf) - 1:
            document.add_page_break()

    pdf.close()
    document.save(destination)


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
