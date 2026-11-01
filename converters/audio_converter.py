"""Audio conversions and bitrate control using FFmpeg."""

from .common import ConversionFailure, run, tool


AUDIO_INPUTS = {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".opus"}
AUDIO_OUTPUTS = {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg"}
AUDIO_CODECS = {
    ".mp3": "libmp3lame", ".wav": "pcm_s16le", ".aac": "aac",
    ".m4a": "aac", ".flac": "flac", ".ogg": "libvorbis",
}


def audio_convert(source, output, source_ext, target, compression=None):
    if target not in AUDIO_OUTPUTS:
        raise ConversionFailure(f"Conversion from {source_ext} to {target} is not supported.")
    args = [tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", str(source), "-vn", "-c:a", AUDIO_CODECS[target]]
    if target != ".wav":
        bitrate = max(64, round(320 - compression * 2.56)) if compression is not None else 192
        args.extend(["-b:a", f"{bitrate}k"])
    args.append(str(output))
    run(args)
