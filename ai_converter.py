"""Backward-compatible imports for older callers.

New conversion code lives in the differentiated ``converters`` package:
``image_converter``, ``pdf_converter``, ``office_converter``, ``text_converter``,
``audio_converter``, ``video_converter``, ``archive_converter``,
``ocr_converter``, and ``router``.
"""

from converters.archive_converter import ARCHIVE_INPUTS, ARCHIVE_OUTPUTS, archive_convert, safe_member
from converters.audio_converter import AUDIO_INPUTS, AUDIO_OUTPUTS, audio_convert
from converters.common import ConversionFailure, run
from converters.image_converter import IMAGE_INPUTS, IMAGE_OUTPUTS, image_convert
from converters.media_converter import media_convert
from converters.office_converter import OFFICE_INPUTS, OFFICE_OUTPUTS, office_convert
from converters.pdf_converter import pdf_compress, pdf_pages, pdf_to_editable_docx, output_extension
from converters.router import convert_file
from converters.text_converter import text_pdf
from converters.video_converter import VIDEO_INPUTS, VIDEO_OUTPUTS, video_convert

__all__ = [
    "ARCHIVE_INPUTS", "ARCHIVE_OUTPUTS", "AUDIO_INPUTS", "AUDIO_OUTPUTS",
    "ConversionFailure", "IMAGE_INPUTS", "IMAGE_OUTPUTS", "OFFICE_INPUTS",
    "OFFICE_OUTPUTS", "VIDEO_INPUTS", "VIDEO_OUTPUTS", "archive_convert",
    "audio_convert", "convert_file", "image_convert", "media_convert", "office_convert",
    "output_extension", "pdf_compress", "pdf_pages", "pdf_to_editable_docx",
    "run", "safe_member", "text_pdf", "video_convert",
]
