"""Compatibility facade for callers that previously shared one media module."""

from .audio_converter import AUDIO_INPUTS, AUDIO_OUTPUTS, audio_convert
from .video_converter import VIDEO_INPUTS, VIDEO_OUTPUTS, video_convert


def media_convert(source, output, source_ext, target, compression=None, quality=85):
    if source_ext in VIDEO_INPUTS:
        return video_convert(source, output, source_ext, target, compression, quality)
    return audio_convert(source, output, source_ext, target, compression)
