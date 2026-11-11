"""Video format conversion, audio extraction, and GIF generation."""

from .audio_converter import AUDIO_OUTPUTS
from .common import ConversionFailure, run, tool


VIDEO_INPUTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
VIDEO_OUTPUTS = {".mp4", ".mov", ".mkv", ".webm"}


def video_convert(source, output, source_ext, target, compression=None, quality=85):
    if target not in VIDEO_OUTPUTS | AUDIO_OUTPUTS | {".gif"}:
        raise ConversionFailure(f"Conversion from {source_ext} to {target} is not supported.")
    args = [tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", str(source)]
    if target in AUDIO_OUTPUTS:
        codecs = {
            ".mp3": "libmp3lame",
            ".wav": "pcm_s16le",
            ".aac": "aac",
            ".m4a": "aac",
            ".flac": "flac",
            ".ogg": "libvorbis",
        }
        args.extend(["-vn", "-c:a", codecs[target]])
        if target != ".wav":
            bitrate = max(64, round(320 - compression * 2.56)) if compression is not None else 192
            args.extend(["-b:a", f"{bitrate}k"])
    elif target == ".gif":
        fps = max(5, min(20, round(20 * quality / 100)))
        args.extend(["-vf", f"fps={fps},scale=480:-1:flags=lanczos"])
    elif target == ".webm":
        args.extend(
            [
                "-c:v",
                "libvpx-vp9",
                "-crf",
                str(35 - round(quality / 10)),
                "-b:v",
                "0",
                "-c:a",
                "libopus",
            ]
        )
    else:
        crf = (
            18 + round((100 - quality) / 5) if compression is None else 18 + round(compression / 8)
        )
        args.extend(
            ["-c:v", "libx264", "-crf", str(min(35, crf)), "-preset", "medium", "-c:a", "aac"]
        )
    args.append(str(output))
    run(args)
