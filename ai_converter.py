"""Format-selected conversions used by the OptiConverter AI mode endpoint."""

import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass


IMAGE_INPUTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
IMAGE_OUTPUTS = {".png", ".jpg", ".jpeg", ".webp"}
OFFICE_INPUTS = {".docx", ".pptx", ".xlsx", ".xls", ".csv"}
OFFICE_OUTPUTS = {".pdf", ".csv", ".xlsx", ".xls"}
VIDEO_INPUTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
VIDEO_OUTPUTS = {".mp4", ".mov", ".mkv", ".webm"}
AUDIO_INPUTS = {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".opus"}
AUDIO_OUTPUTS = {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg"}
ARCHIVE_INPUTS = {".zip", ".tar", ".tar.gz", ".tgz"}
ARCHIVE_OUTPUTS = {".zip", ".tar", ".tar.gz"}


class ConversionFailure(ValueError):
    pass


def run(command):
    try:
        return subprocess.run(command, check=True, capture_output=True, timeout=120)
    except FileNotFoundError as exc:
        raise ConversionFailure(f"{Path(command[0]).name} is required for this conversion.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ConversionFailure("The conversion exceeded the two-minute time limit.") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()
        raise ConversionFailure(detail or "The conversion tool could not process this file.") from exc


def image_convert(source, output, target, quality):
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            image.seek(0)
            if target == ".pdf":
                image.convert("RGB").save(output, "PDF", resolution=150)
            else:
                fmt = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}[target]
                if fmt == "JPEG":
                    if image.mode in {"RGBA", "LA"}:
                        rgba = image.convert("RGBA")
                        background = Image.new("RGB", rgba.size, "white")
                        background.paste(rgba, mask=rgba.getchannel("A"))
                        image = background
                    else:
                        image = image.convert("RGB")
                elif fmt == "PNG" and image.mode not in {"RGB", "RGBA", "L", "LA"}:
                    image = image.convert("RGBA")
                elif fmt == "WEBP" and image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA")
                options = {"quality": quality} if fmt in {"JPEG", "WEBP"} else {}
                image.save(output, fmt, **options)
    except (OSError, ValueError) as exc:
        raise ConversionFailure("The image format could not be decoded. HEIC files require pillow-heif support.") from exc


def pdf_pages(source, output, target, output_stem=None):
    directory = output.parent
    prefix = directory / "page"
    extension = "jpg" if target in {".jpg", ".jpeg"} else "png"
    args = [shutil.which("pdftoppm") or "pdftoppm", "-r", "150"]
    args.append("-jpeg" if extension == "jpg" else "-png")
    args.extend([str(source), str(prefix)])
    run(args)
    pages = sorted(directory.glob(f"page-*.{extension}"), key=lambda p: int(p.stem.split("-")[-1]))
    if not pages:
        raise ConversionFailure("No pages were rendered from this PDF.")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, page in enumerate(pages, 1):
            archive.write(page, f"{output_stem or source.stem}-{index}.{extension}")


def pdf_compress(source, output):
    compressed = output.with_name("compressed.pdf")
    run([shutil.which("gs") or "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
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
            run([shutil.which("pdftoppm") or "pdftoppm", "-f", str(index + 1), "-l", str(index + 1),
                 "-r", "180", "-png", str(source), str(prefix)])
            rendered = next(Path(directory).glob(f"ocr-page-{index + 1}-*.png"), None)
            if rendered:
                result = run([shutil.which("tesseract") or "tesseract", str(rendered), "stdout", "tsv"])
                ocr_layout_pages[index] = result.stdout.decode(errors="replace")
                ocr_pages[index] = ocr_tsv_to_text(ocr_layout_pages[index])
    finally:
        pdf.close()

    ocr_path = Path(directory) / "ocr.txt"
    if any(page.strip() for page in ocr_pages):
        ocr_path.write_text("\f".join(ocr_pages), encoding="utf-8")
        ocr_layout_path = Path(directory) / "ocr-layout.json"
        ocr_layout_path.write_text(json.dumps(ocr_layout_pages), encoding="utf-8")
        convert(source, output, str(ocr_path), str(ocr_layout_path))
    else:
        convert(source, output)


def office_convert(source, output, target, directory):
    profile = Path(directory) / "lo-profile"
    run([shutil.which("libreoffice") or "libreoffice", "--headless",
         f"-env:UserInstallation=file://{profile}", "--convert-to", target.lstrip("."),
         "--outdir", str(output.parent), str(source)])
    generated = output.parent / f"{source.stem}{target}"
    if not generated.exists():
        raise ConversionFailure(f"LibreOffice did not produce a {target[1:].upper()} file.")
    if generated != output:
        generated.replace(output)


def text_pdf(source, output):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen.canvas import Canvas
    canvas = Canvas(str(output), pagesize=letter)
    width, height = letter
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


def media_convert(source, output, source_ext, target, compression, quality):
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    args = [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(source)]
    audio_codecs = {".mp3": "libmp3lame", ".wav": "pcm_s16le", ".aac": "aac", ".m4a": "aac", ".flac": "flac", ".ogg": "libvorbis"}
    if target in AUDIO_OUTPUTS:
        args.extend(["-vn", "-c:a", audio_codecs[target]])
        if target != ".wav":
            bitrate = max(64, round(320 - compression * 2.56)) if compression is not None else 192
            args.extend(["-b:a", f"{bitrate}k"])
    elif target == ".gif":
        fps = max(5, min(20, round(20 * quality / 100)))
        args.extend(["-vf", f"fps={fps},scale=480:-1:flags=lanczos"])
    elif target in VIDEO_OUTPUTS:
        if target == ".webm":
            args.extend(["-c:v", "libvpx-vp9", "-crf", str(35 - round(quality / 10)), "-b:v", "0", "-c:a", "libopus"])
        else:
            crf = 18 + round((100 - quality) / 5) if compression is None else 18 + round(compression / 8)
            args.extend(["-c:v", "libx264", "-crf", str(min(35, crf)), "-preset", "medium", "-c:a", "aac"])
    else:
        raise ConversionFailure(f"Conversion from {source_ext} to {target} is not supported.")
    args.append(str(output))
    run(args)


def safe_member(name):
    path = PurePosixPath(name)
    return not path.is_absolute() and all(part not in {"..", "."} for part in path.parts) and "\\" not in name


def archive_convert(source, output, target):
    entries = []
    total_size = 0
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for item in archive.infolist():
                if item.is_dir() or not safe_member(item.filename):
                    continue
                total_size += item.file_size
                if total_size > 100 * 1024 * 1024:
                    raise ConversionFailure("Expanded archive contents exceed 100 MB.")
                entries.append((item.filename, archive.read(item)))
    else:
        try:
            with tarfile.open(source, "r:*") as archive:
                for item in archive.getmembers():
                    if not item.isfile() or not safe_member(item.name):
                        continue
                    total_size += item.size
                    if total_size > 100 * 1024 * 1024:
                        raise ConversionFailure("Expanded archive contents exceed 100 MB.")
                    stream = archive.extractfile(item)
                    if stream:
                        entries.append((item.name, stream.read()))
        except tarfile.TarError as exc:
            raise ConversionFailure("Could not read this archive.") from exc

    if not entries:
        raise ConversionFailure("This archive contains no convertible files.")
    if target == ".zip":
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries:
                archive.writestr(name, content)
    else:
        mode = "w:gz" if target == ".tar.gz" else "w"
        with tarfile.open(output, mode) as archive:
            for name, content in entries:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(content))


def convert_file(source, output, input_name, target, quality=85, compression=None, compress=False):
    source_ext = ".tar.gz" if input_name.lower().endswith(".tar.gz") else Path(input_name).suffix.lower()
    target = target.lower()
    if compress:
        if source_ext == ".pdf" and target == source_ext:
            pdf_compress(source, output)
        elif source_ext in IMAGE_INPUTS and target in IMAGE_OUTPUTS:
            image_convert(source, output, target, max(30, 100 - (compression or 30)))
        elif source_ext in {".docx", ".pptx", ".xlsx"} and target == source_ext:
            with zipfile.ZipFile(source) as original:
                with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as packed:
                    for name in original.namelist():
                        if safe_member(name):
                            packed.writestr(name, original.read(name))
        elif source_ext in VIDEO_INPUTS | AUDIO_INPUTS and target == source_ext:
            if source_ext == ".wav":
                raise ConversionFailure("WAV audio cannot be compressed while keeping the WAV format.")
            media_convert(source, output, source_ext, target, compression or 30, quality)
        elif source_ext in ARCHIVE_INPUTS and target in ARCHIVE_OUTPUTS | {source_ext}:
            normalized_target = ".tar.gz" if target == ".tgz" else target
            archive_convert(source, output, normalized_target)
        else:
            raise ConversionFailure(f"Compression for {source_ext[1:].upper()} files is not available.")
    elif source_ext == ".pdf":
        if target == ".docx":
            pdf_to_editable_docx(source, output, output.parent)
        elif target == ".pdf":
            pdf_compress(source, output)
        elif target in {".jpg", ".jpeg", ".png"}:
            source_stem = input_name[:-4] if input_name.lower().endswith(".pdf") else source.stem
            pdf_pages(source, output, target, source_stem)
        else:
            raise ConversionFailure(f"Conversion from {source_ext} to {target} is not supported.")
    elif source_ext in IMAGE_INPUTS:
        if target not in IMAGE_OUTPUTS | {".pdf"}:
            raise ConversionFailure(f"Conversion from {source_ext} to {target} is not supported.")
        image_convert(source, output, target, quality)
    elif source_ext in OFFICE_INPUTS:
        if target not in OFFICE_OUTPUTS:
            raise ConversionFailure(f"Conversion from {source_ext} to {target} is not supported.")
        office_convert(source, output, target, output.parent)
    elif source_ext == ".txt" and target == ".pdf":
        text_pdf(source, output)
    elif source_ext in VIDEO_INPUTS and target in VIDEO_OUTPUTS | AUDIO_OUTPUTS | {".gif"}:
        media_convert(source, output, source_ext, target, compression, quality)
    elif source_ext in AUDIO_INPUTS and target in AUDIO_OUTPUTS:
        media_convert(source, output, source_ext, target, compression, quality)
    elif source_ext in ARCHIVE_INPUTS and target in ARCHIVE_OUTPUTS:
        archive_convert(source, output, target)
    else:
        raise ConversionFailure(f"Conversion from {source_ext or 'this file type'} to {target} is not supported.")


def output_extension(source_ext, target):
    if source_ext == ".pdf" and target in {".jpg", ".jpeg", ".png"}:
        return ".zip"
    return target
