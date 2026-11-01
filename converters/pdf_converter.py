"""PDF conversion, compression, page rendering, and editable Word export."""

import json
import shutil
import zipfile
from pathlib import Path

from .common import ConversionFailure, run, tool


def pdf_pages(source, output, target, output_stem=None):
    prefix = output.parent / "page"
    extension = "jpg" if target in {".jpg", ".jpeg"} else "png"
    args = [tool("pdftoppm"), "-r", "150", "-jpeg" if extension == "jpg" else "-png"]
    args.extend([str(source), str(prefix)])
    run(args)
    pages = sorted(output.parent.glob(f"page-*.{extension}"), key=lambda path: int(path.stem.split("-")[-1]))
    if not pages:
        raise ConversionFailure("No pages were rendered from this PDF.")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, page in enumerate(pages, 1):
            archive.write(page, f"{output_stem or source.stem}-{index}.{extension}")


def pdf_compress(source, output):
    compressed = output.with_name("compressed.pdf")
    run([tool("gs"), "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
         "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dQUIET", "-dBATCH",
         f"-sOutputFile={compressed}", str(source)])
    shutil.copyfile(compressed if compressed.stat().st_size < source.stat().st_size else source, output)


def pdf_to_editable_docx(source, output, directory):
    import fitz

    from convert_pdf_to_docx import convert, ocr_tsv_to_text

    pdf = fitz.open(source)
    ocr_pages = [""] * pdf.page_count
    ocr_layout_pages = [""] * pdf.page_count
    try:
        for index, page in enumerate(pdf):
            if page.get_text().strip():
                continue
            prefix = Path(directory) / f"ocr-page-{index + 1}"
            run([tool("pdftoppm"), "-f", str(index + 1), "-l", str(index + 1),
                 "-r", "180", "-png", str(source), str(prefix)])
            rendered = next(Path(directory).glob(f"ocr-page-{index + 1}-*.png"), None)
            if rendered:
                result = run([tool("tesseract"), str(rendered), "stdout", "tsv"])
                ocr_layout_pages[index] = result.stdout.decode(errors="replace")
                ocr_pages[index] = ocr_tsv_to_text(ocr_layout_pages[index])
    finally:
        pdf.close()

    if any(page.strip() for page in ocr_pages):
        ocr_path = Path(directory) / "ocr.txt"
        ocr_path.write_text("\f".join(ocr_pages), encoding="utf-8")
        ocr_layout_path = Path(directory) / "ocr-layout.json"
        ocr_layout_path.write_text(json.dumps(ocr_layout_pages), encoding="utf-8")
        convert(source, output, str(ocr_path), str(ocr_layout_path))
    else:
        convert(source, output)


def output_extension(source_ext, target):
    if source_ext == ".pdf" and target in {".jpg", ".jpeg", ".png"}:
        return ".zip"
    return target
