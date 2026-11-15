from io import BytesIO
from pathlib import Path
import zipfile

import pytest
from docx import Document
from PIL import Image
from reportlab.pdfgen.canvas import Canvas

from opticonverterbackend.convert_pdf_to_docx import convert
from opticonverterbackend.converters import pdf_converter


def test_pdf_to_word_keeps_separate_text_columns_on_the_same_row(tmp_path):
    source = tmp_path / "columns.pdf"
    output = tmp_path / "columns.docx"
    canvas = Canvas(str(source), pagesize=(612, 792))
    canvas.drawString(72, 720, "Order 1001")
    canvas.drawRightString(540, 720, "AMOUNT $125.50")
    canvas.drawString(72, 690, "Description")
    canvas.drawString(420, 690, "Total")
    canvas.save()

    convert(source, output)

    paragraphs = [paragraph for paragraph in Document(output).paragraphs if paragraph.text.strip()]
    assert len(paragraphs) == 2
    assert paragraphs[0].text == "Order 1001 \tAMOUNT $125.50"
    assert paragraphs[1].text == "Description \tTotal"


@pytest.mark.parametrize(
    ("target", "image_format", "extension"), [(".jpg", "JPEG", ".jpg"), (".png", "PNG", ".png")]
)
def test_pdf_page_images_are_valid_files_inside_zip(
    tmp_path, monkeypatch, target, image_format, extension
):
    source = tmp_path / "source.pdf"
    output = tmp_path / "pages.zip"
    source.write_bytes(b"test PDF placeholder")

    def fake_render(arguments):
        prefix = Path(arguments[-1])
        rendered = prefix.with_name(f"{prefix.name}-1{extension}")
        Image.new("RGB", (32, 24), "white").save(rendered, format=image_format)

    monkeypatch.setattr(pdf_converter, "tool", lambda _name: "pdftoppm")
    monkeypatch.setattr(pdf_converter, "run", fake_render)

    pdf_converter.pdf_pages(source, output, target, "source")

    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        image_files = archive.namelist()
        assert image_files == [f"source-1{extension}"]
        with Image.open(BytesIO(archive.read(image_files[0]))) as image:
            image.verify()
            assert image.format == image_format
