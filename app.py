import io
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from flask import Flask, jsonify, request, send_file
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from docx import Document
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from ai_converter import (
    ARCHIVE_INPUTS,
    ARCHIVE_OUTPUTS,
    AUDIO_INPUTS,
    AUDIO_OUTPUTS,
    IMAGE_INPUTS,
    IMAGE_OUTPUTS,
    OFFICE_INPUTS,
    OFFICE_OUTPUTS,
    VIDEO_INPUTS,
    VIDEO_OUTPUTS,
    ConversionFailure,
    convert_file,
    output_extension,
)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
CONVERSION_TIMEOUT_SECONDS = 120
MAX_BATCH_FILES = 20

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
allowed_origins = {
    origin.strip()
    for origin in os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173").split(",")
    if origin.strip()
}


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Vary"] = "Origin"
    return response


def fail(message, status=400):
    return jsonify(detail=message), status


def tool(name):
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} is required but is not installed.")
    return path


def run(command):
    try:
        return subprocess.run(
            command, check=True, capture_output=True, timeout=CONVERSION_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("The conversion exceeded the two-minute time limit.") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()
        raise ValueError(detail or "The conversion tool could not process this file.") from exc


def checked_upload(field="file"):
    uploaded = request.files.get(field)
    if not uploaded or not uploaded.filename:
        raise ValueError("Upload a PDF in the file field.")
    suffix = Path(uploaded.filename).suffix.lower()
    if suffix != ".pdf":
        raise ValueError("Only PDF files are supported.")
    directory = tempfile.mkdtemp(prefix="opticonverter-")
    source = Path(directory) / "input.pdf"
    uploaded.save(source)
    if source.stat().st_size == 0:
        shutil.rmtree(directory, ignore_errors=True)
        raise ValueError("The uploaded file is empty.")
    return directory, source, Path(uploaded.filename).stem


def checked_output(path):
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError("The conversion did not produce an output file.")
    if path.stat().st_size > MAX_OUTPUT_BYTES:
        raise ValueError("The converted file is larger than the 100 MB output limit.")


def safe_stem(filename, fallback):
    stem = Path(filename).stem.strip()
    return stem or fallback


def text_to_pdf(source, output):
    canvas = Canvas(str(output))
    width, height = 612, 792
    y = height - 48
    canvas.setFont("Helvetica", 11)
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines() or [""]:
        if y < 48:
            canvas.showPage()
            canvas.setFont("Helvetica", 11)
            y = height - 48
        canvas.drawString(48, y, line[:120])
        y -= 16
    canvas.save()


def docx_to_pdf(source, output):
    document = Document(str(source))
    canvas = Canvas(str(output))
    width, height = 612, 792
    y = height - 48
    canvas.setFont("Helvetica", 11)
    for paragraph in document.paragraphs:
        for line in (paragraph.text or "").splitlines() or [""]:
            if y < 48:
                canvas.showPage()
                canvas.setFont("Helvetica", 11)
                y = height - 48
            canvas.drawString(48, y, line[:120])
            y -= 16
    canvas.save()


def image_to_pdf(source, output):
    image = ImageReader(str(source))
    image_width, image_height = image.getSize()
    page_width, page_height = 612, 792
    scale = min((page_width - 72) / image_width, (page_height - 72) / image_height, 1)
    width, height = image_width * scale, image_height * scale
    canvas = Canvas(str(output), pagesize=(page_width, page_height))
    canvas.drawImage(image, (page_width - width) / 2, (page_height - height) / 2,
                     width=width, height=height, preserveAspectRatio=True)
    canvas.save()


def normalize_to_pdf(uploaded, directory, index):
    filename = uploaded.filename or ""
    suffix = Path(filename).suffix.lower()
    supported = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".txt", ".md"}
    if suffix not in supported:
        raise ValueError(
            "Supported input files are PDF, DOCX, PNG, JPG, TXT, and Markdown."
        )
    source = Path(directory) / f"input-{index}{suffix}"
    uploaded.save(source)
    if source.stat().st_size == 0:
        raise ValueError(f"{filename} is empty.")
    normalized = Path(directory) / f"normalized-{index}.pdf"
    if suffix == ".pdf":
        shutil.copyfile(source, normalized)
    elif suffix == ".docx":
        docx_to_pdf(source, normalized)
    elif suffix in {".png", ".jpg", ".jpeg"}:
        image_to_pdf(source, normalized)
    else:
        text_to_pdf(source, normalized)
    checked_output(normalized)
    return normalized, safe_stem(filename, f"document-{index}")


def batch_output(source, output, target):
    if target in (".jpg", ".png"):
        zip_pages(source, output, target[1:])
    elif target == ".docx":
        pdf_to_docx(source, output)
    elif target == ".pdf":
        compress_pdf(source, output)
    else:
        raise ValueError("Choose .jpg, .png, .docx, or .pdf.")


def zip_pages(source, output, extension):
    prefix = output.parent / "page"
    renderer = "-jpeg" if extension == "jpg" else "-png"
    run([tool("pdftoppm"), "-r", "150", renderer, str(source), str(prefix)])
    pages = sorted(output.parent.glob(f"page-*.{extension}"))
    if not pages:
        raise ValueError("No pages were rendered from the PDF.")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, page in enumerate(pages, 1):
            archive.write(page, f"{source.stem}-{index}.{extension}")


def pdf_to_docx(source, output):
    reader = PdfReader(str(source))
    document = Document()
    for index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if not text.strip():
            with tempfile.TemporaryDirectory(prefix="opticonverter-ocr-") as directory:
                image = Path(directory) / "page"
                run([tool("pdftoppm"), "-f", str(index + 1), "-l", str(index + 1),
                     "-r", "150", "-png", str(source), str(image)])
                rendered = next(Path(directory).glob("page-*.png"), None)
                if rendered:
                    ocr = run([tool("tesseract"), str(rendered), "stdout"])
                    text = ocr.stdout.decode(errors="replace")
        if index:
            document.add_page_break()
        for paragraph in text.splitlines():
            document.add_paragraph(paragraph)
    document.save(output)


def compress_pdf(source, output):
    temporary = output.with_name("compressed.pdf")
    run([tool("gs"), "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
         "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dQUIET", "-dBATCH",
         f"-sOutputFile={temporary}", str(source)])
    shutil.copyfile(temporary if temporary.stat().st_size < source.stat().st_size else source, output)


def send_result(directory, output, download_name):
    checked_output(output)
    response = send_file(output, as_attachment=True, download_name=download_name)
    @response.call_on_close
    def cleanup():
        shutil.rmtree(directory, ignore_errors=True)
    return response


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/formats")
def formats():
    return jsonify({
        "pdf": [".jpg", ".png", ".docx", ".pdf"],
        "ai": {
            "endpoint": "/ai-convert",
            "input": sorted({".pdf", ".txt"} | IMAGE_INPUTS | OFFICE_INPUTS | VIDEO_INPUTS | AUDIO_INPUTS | ARCHIVE_INPUTS),
            "output": sorted(IMAGE_OUTPUTS | OFFICE_OUTPUTS | VIDEO_OUTPUTS | AUDIO_OUTPUTS | ARCHIVE_OUTPUTS | {".pdf", ".gif", ".docx"}),
        },
        "operations": ["merge", "split", "rotate", "watermark", "metadata"],
    })


@app.post("/convert")
def convert():
    directory = source = None
    try:
        directory, source, stem = checked_upload()
        target = request.args.get("format", "").lower()
        output = Path(directory) / f"{stem}{'.zip' if target in ('.jpg', '.png') else target}"
        if target in (".jpg", ".png"):
            zip_pages(source, output, target[1:])
        elif target == ".docx":
            pdf_to_docx(source, output)
        elif target == ".pdf":
            compress_pdf(source, output)
        else:
            raise ValueError("Choose .jpg, .png, .docx, or .pdf.")
        return send_result(directory, output, output.name)
    except (ValueError, RuntimeError) as exc:
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
        return fail(str(exc), 422)


@app.post("/ai-convert")
def ai_mode_convert():
    directory = None
    try:
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            raise ValueError("Upload one file in the file field.")
        input_name = Path(uploaded.filename).name
        lower_name = input_name.lower()
        input_ext = ".tar.gz" if lower_name.endswith(".tar.gz") else Path(lower_name).suffix
        target = request.args.get("format", "").lower()
        if not input_ext or not target:
            raise ValueError("Choose an input file and an output format from the dropdown.")

        quality = request.args.get("quality", "85")
        if not quality.isdigit() or not 30 <= int(quality) <= 100:
            raise ValueError("Image quality must be an integer between 30 and 100.")
        compression_value = request.args.get("compression", "auto").lower()
        compression = None if compression_value == "auto" else int(compression_value)
        if compression is not None and not 0 <= compression <= 90:
            raise ValueError("Compression must be an integer between 0 and 90 percent.")
        compress = request.args.get("compress", "").lower() == "any"

        directory = tempfile.mkdtemp(prefix="opticonverter-ai-")
        source = Path(directory) / f"source{input_ext}"
        uploaded.save(source)
        if source.stat().st_size == 0:
            raise ValueError("The uploaded file is empty.")
        output_ext = output_extension(input_ext, target)
        stem = input_name[:-7] if lower_name.endswith(".tar.gz") else Path(input_name).stem
        prefix = f"{stem}-compressed" if compress or (input_ext == ".pdf" and target == ".pdf") else stem
        output = Path(directory) / f"{safe_stem(prefix, 'converted')}{output_ext}"
        convert_file(source, output, input_name, target, int(quality), compression, compress)
        checked_output(output)
        return send_result(directory, output, output.name)
    except (ValueError, RuntimeError, OSError, ConversionFailure) as exc:
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
        return fail(str(exc), 422)


@app.post("/ai/convert")
@app.post("/convert/batch")
def ai_convert():
    directory = tempfile.mkdtemp(prefix="opticonverter-ai-")
    try:
        files = [uploaded for uploaded in request.files.getlist("files")
                 if uploaded and uploaded.filename]
        if not files:
            raise ValueError("Upload one or more documents in the files field.")
        if len(files) > MAX_BATCH_FILES:
            raise ValueError(f"Upload no more than {MAX_BATCH_FILES} documents at once.")
        target = request.args.get("format", "").lower()
        if target not in {".jpg", ".png", ".docx", ".pdf"}:
            raise ValueError("Choose .jpg, .png, .docx, or .pdf.")

        archive_path = Path(directory) / "converted-documents.zip"
        used_names = set()
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for index, uploaded in enumerate(files, 1):
                source, stem = normalize_to_pdf(uploaded, directory, index)
                output_suffix = ".zip" if target in {".jpg", ".png"} else target
                output = Path(directory) / f"output-{index}{output_suffix}"
                batch_output(source, output, target)
                checked_output(output)
                name = f"{stem}{output_suffix}"
                if name in used_names:
                    name = f"{stem}-{index}{output_suffix}"
                used_names.add(name)
                archive.write(output, name)
        return send_result(directory, archive_path, "converted-documents.zip")
    except (ValueError, RuntimeError, OSError, PdfReadError) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        return fail(str(exc), 422)


@app.post("/pdf/merge")
def merge():
    directory = tempfile.mkdtemp(prefix="opticonverter-")
    try:
        files = request.files.getlist("files")
        if len(files) < 2:
            raise ValueError("Upload at least two PDF files in the files field.")
        writer = PdfWriter()
        for uploaded in files:
            if not uploaded.filename.lower().endswith(".pdf"):
                raise ValueError("Only PDF files are supported.")
            reader = PdfReader(io.BytesIO(uploaded.read(MAX_UPLOAD_BYTES + 1)))
            for page in reader.pages:
                writer.add_page(page)
        output = Path(directory) / "merged.pdf"
        with output.open("wb") as stream:
            writer.write(stream)
        return send_result(directory, output, "merged.pdf")
    except (ValueError, RuntimeError, OSError, PdfReadError) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        return fail(str(exc), 422)


@app.post("/pdf/split")
def split():
    directory = source = None
    try:
        directory, source, stem = checked_upload()
        reader = PdfReader(str(source))
        start = int(request.form.get("start", "1"))
        end = int(request.form.get("end", str(len(reader.pages))))
        if start < 1 or end < start or end > len(reader.pages):
            raise ValueError("The page range is invalid.")
        output = Path(directory) / f"{stem}-pages-{start}-{end}.pdf"
        writer = PdfWriter()
        for page in reader.pages[start - 1:end]:
            writer.add_page(page)
        with output.open("wb") as stream:
            writer.write(stream)
        return send_result(directory, output, output.name)
    except (ValueError, RuntimeError) as exc:
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
        return fail(str(exc), 422)


@app.post("/pdf/rotate")
def rotate():
    directory = source = None
    try:
        directory, source, stem = checked_upload()
        degrees = int(request.form.get("degrees", "90"))
        if degrees % 90:
            raise ValueError("Rotation must be a multiple of 90 degrees.")
        reader, writer = PdfReader(str(source)), PdfWriter()
        for page in reader.pages:
            writer.add_page(page.rotate(degrees))
        output = Path(directory) / f"{stem}-rotated.pdf"
        with output.open("wb") as stream:
            writer.write(stream)
        return send_result(directory, output, output.name)
    except (ValueError, RuntimeError) as exc:
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
        return fail(str(exc), 422)


@app.post("/pdf/metadata")
def metadata():
    directory = source = None
    try:
        directory, source, stem = checked_upload()
        reader, writer = PdfReader(str(source)), PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_metadata({f"/{key}": value for key, value in request.form.items()})
        output = Path(directory) / f"{stem}-metadata.pdf"
        with output.open("wb") as stream:
            writer.write(stream)
        return send_result(directory, output, output.name)
    except (ValueError, RuntimeError) as exc:
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
        return fail(str(exc), 422)


@app.post("/pdf/watermark")
def watermark():
    directory = source = None
    try:
        directory, source, stem = checked_upload()
        text = request.form.get("text", "").strip()
        if not text:
            raise ValueError("Provide watermark text.")
        reader, writer = PdfReader(str(source)), PdfWriter()
        for page in reader.pages:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            overlay = Path(directory) / "watermark.pdf"
            canvas = Canvas(str(overlay), pagesize=(width, height))
            canvas.saveState()
            canvas.setFillAlpha(0.25)
            canvas.setFont("Helvetica", 36)
            canvas.translate(width / 2, height / 2)
            canvas.rotate(45)
            canvas.drawCentredString(0, 0, text[:200])
            canvas.restoreState()
            canvas.save()
            watermark_page = PdfReader(str(overlay)).pages[0]
            page.merge_page(watermark_page)
            writer.add_page(page)
        output = Path(directory) / f"{stem}-watermarked.pdf"
        with output.open("wb") as stream:
            writer.write(stream)
        return send_result(directory, output, output.name)
    except (ValueError, RuntimeError) as exc:
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
        return fail(str(exc), 422)


@app.errorhandler(413)
def too_large(_error):
    return fail("Files must be 50 MB or smaller.", 413)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
