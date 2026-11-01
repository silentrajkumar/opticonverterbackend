"""Plain-text and Markdown document conversion."""

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas


def text_pdf(source, output):
    canvas = Canvas(str(output), pagesize=letter)
    _width, height = letter
    y = height - 48
    canvas.setFont("Helvetica", 10)
    text = source.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines() or [""]:
        for offset in range(0, max(len(line), 1), 110):
            if y < 48:
                canvas.showPage()
                canvas.setFont("Helvetica", 10)
                y = height - 48
            canvas.drawString(48, y, line[offset:offset + 110])
            y -= 14
    canvas.save()
