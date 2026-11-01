"""ZIP and TAR archive repacking."""

import io
import tarfile
import zipfile
from pathlib import PurePosixPath

from .common import ConversionFailure


ARCHIVE_INPUTS = {".zip", ".tar", ".tar.gz", ".tgz"}
ARCHIVE_OUTPUTS = {".zip", ".tar", ".tar.gz"}
MAX_EXPANDED_ARCHIVE_BYTES = 100 * 1024 * 1024


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
                if total_size > MAX_EXPANDED_ARCHIVE_BYTES:
                    raise ConversionFailure("Expanded archive contents exceed 100 MB.")
                entries.append((item.filename, archive.read(item)))
    else:
        try:
            with tarfile.open(source, "r:*") as archive:
                for item in archive.getmembers():
                    if not item.isfile() or not safe_member(item.name):
                        continue
                    total_size += item.size
                    if total_size > MAX_EXPANDED_ARCHIVE_BYTES:
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
        return

    mode = "w:gz" if target == ".tar.gz" else "w"
    with tarfile.open(output, mode) as archive:
        for name, content in entries:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
