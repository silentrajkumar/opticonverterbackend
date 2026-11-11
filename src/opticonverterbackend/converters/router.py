"""Select the appropriate file-type converter for one conversion request."""

import zipfile
from pathlib import Path

from .archive_converter import ARCHIVE_INPUTS, ARCHIVE_OUTPUTS, archive_convert, safe_member
from .audio_converter import AUDIO_INPUTS, AUDIO_OUTPUTS, audio_convert
from .common import ConversionFailure
from .image_converter import IMAGE_INPUTS, IMAGE_OUTPUTS, image_convert
from .office_converter import OFFICE_INPUTS, OFFICE_OUTPUTS, office_convert
from .pdf_converter import pdf_compress, pdf_pages, pdf_to_editable_docx
from .text_converter import text_pdf
from .video_converter import VIDEO_INPUTS, VIDEO_OUTPUTS, video_convert


def convert_file(source, output, input_name, target, quality=85, compression=None, compress=False):
    source_ext = (
        ".tar.gz" if input_name.lower().endswith(".tar.gz") else Path(input_name).suffix.lower()
    )
    target = target.lower()

    if compress:
        _compress_file(source, output, source_ext, target, compression, quality)
    elif source_ext == ".pdf":
        _convert_pdf(source, output, target, input_name)
    elif source_ext in IMAGE_INPUTS:
        if target not in IMAGE_OUTPUTS | {".pdf"}:
            raise ConversionFailure(f"Conversion from {source_ext} to {target} is not supported.")
        image_convert(source, output, target, quality)
    elif source_ext in OFFICE_INPUTS:
        if target not in OFFICE_OUTPUTS:
            raise ConversionFailure(f"Conversion from {source_ext} to {target} is not supported.")
        office_convert(source, output, target, output.parent)
    elif source_ext in {".txt", ".md"} and target == ".pdf":
        text_pdf(source, output)
    elif source_ext in VIDEO_INPUTS and target in VIDEO_OUTPUTS | AUDIO_OUTPUTS | {".gif"}:
        video_convert(source, output, source_ext, target, compression, quality)
    elif source_ext in AUDIO_INPUTS and target in AUDIO_OUTPUTS:
        audio_convert(source, output, source_ext, target, compression)
    elif source_ext in ARCHIVE_INPUTS and target in ARCHIVE_OUTPUTS:
        archive_convert(source, output, target)
    else:
        raise ConversionFailure(
            f"Conversion from {source_ext or 'this file type'} to {target} is not supported."
        )


def _convert_pdf(source, output, target, input_name):
    if target == ".docx":
        pdf_to_editable_docx(source, output, output.parent)
    elif target == ".pdf":
        pdf_compress(source, output)
    elif target in {".jpg", ".jpeg", ".png"}:
        source_stem = (
            input_name[:-4] if input_name.lower().endswith(".pdf") else Path(input_name).stem
        )
        pdf_pages(source, output, target, source_stem)
    else:
        raise ConversionFailure(f"Conversion from .pdf to {target} is not supported.")


def _compress_file(source, output, source_ext, target, compression, quality):
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
        if source_ext in VIDEO_INPUTS:
            video_convert(source, output, source_ext, target, compression or 30, quality)
        else:
            audio_convert(source, output, source_ext, target, compression or 30)
    elif source_ext in ARCHIVE_INPUTS and target in ARCHIVE_OUTPUTS | {source_ext}:
        archive_target = ".tar.gz" if target == ".tgz" else target
        archive_convert(source, output, archive_target)
    else:
        raise ConversionFailure(f"Compression for {source_ext[1:].upper()} files is not available.")


def output_extension(source_ext, target):
    if source_ext == ".pdf" and target in {".jpg", ".jpeg", ".png"}:
        return ".zip"
    return target
