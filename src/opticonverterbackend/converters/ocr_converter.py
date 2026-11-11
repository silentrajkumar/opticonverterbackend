"""OCR text extraction for uploaded images."""

from pathlib import Path

from PIL import Image, ImageOps

from .common import ConversionFailure, run, tool


def image_ocr(source, work_directory, max_pixels=40_000_000):
    normalized = Path(work_directory) / "normalized.png"
    try:
        with Image.open(source) as image:
            if image.width * image.height > max_pixels:
                raise ConversionFailure("OCR images must be 40 megapixels or smaller.")
            ImageOps.exif_transpose(image).convert("RGB").save(normalized, "PNG")
    except ConversionFailure:
        raise
    except OSError as exc:
        raise ConversionFailure(
            "The uploaded image is invalid or could not be prepared for OCR."
        ) from exc

    result = run([tool("tesseract"), str(normalized), "stdout"])
    text = result.stdout.decode(errors="replace").strip()
    if not text:
        raise ConversionFailure("OCR did not recognize readable text in this image.")
    if len(text.encode("utf-8")) > 10 * 1024 * 1024:
        raise ConversionFailure("OCR text exceeds the 10 MB response limit.")
    return text
