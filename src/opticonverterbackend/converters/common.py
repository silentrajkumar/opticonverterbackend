"""Shared process helpers and conversion errors."""

import shutil
import subprocess
from pathlib import Path


class ConversionFailure(ValueError):
    """A conversion could not be completed with the supplied file or tools."""


def tool(name):
    path = shutil.which(name)
    if not path:
        raise ConversionFailure(f"{name} is required for this conversion.")
    return path


def run(command, timeout=120):
    try:
        return subprocess.run(command, check=True, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ConversionFailure(
            f"{Path(command[0]).name} is required for this conversion."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ConversionFailure("The conversion exceeded the two-minute time limit.") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()
        raise ConversionFailure(
            detail or "The conversion tool could not process this file."
        ) from exc
