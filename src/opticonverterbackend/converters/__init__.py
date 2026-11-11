"""Conversion engines grouped by file type."""

from .archive_converter import ARCHIVE_INPUTS, ARCHIVE_OUTPUTS
from .audio_converter import AUDIO_INPUTS, AUDIO_OUTPUTS
from .common import ConversionFailure, run, tool
from .image_converter import IMAGE_INPUTS, IMAGE_OUTPUTS
from .office_converter import OFFICE_INPUTS, OFFICE_OUTPUTS
from .router import convert_file, output_extension
from .video_converter import VIDEO_INPUTS, VIDEO_OUTPUTS

__all__ = [
    "ARCHIVE_INPUTS",
    "ARCHIVE_OUTPUTS",
    "AUDIO_INPUTS",
    "AUDIO_OUTPUTS",
    "ConversionFailure",
    "IMAGE_INPUTS",
    "IMAGE_OUTPUTS",
    "OFFICE_INPUTS",
    "OFFICE_OUTPUTS",
    "VIDEO_INPUTS",
    "VIDEO_OUTPUTS",
    "convert_file",
    "output_extension",
    "run",
    "tool",
]
