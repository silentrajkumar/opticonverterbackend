"""Office document and plain-text conversions."""

import os
import shutil
import tempfile
from pathlib import Path

from .common import ConversionFailure, run
from .text_converter import text_pdf


OFFICE_INPUTS = {".docx", ".pptx", ".xlsx", ".xls", ".csv"}
OFFICE_OUTPUTS = {".pdf", ".csv", ".xlsx", ".xls"}


def _word_convert(source, output, target):
    """Use installed Microsoft Word on Windows for higher fidelity conversion."""
    if os.name != "nt":
        return False
    try:
        from win32com.client import DispatchEx
    except ImportError:
        DispatchEx = None

    if DispatchEx is None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            return False
        script = r"""
param([string]$Source, [string]$Output, [string]$Target)
$ErrorActionPreference = 'Stop'
$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open($Source, $false, $true, $false)
    $format = if ($Target -eq '.pdf') { 17 } else { 16 }
    $document.SaveAs2($Output, $format)
} finally {
    if ($document -ne $null) { try { $document.Close($false) } catch {} }
    if ($word -ne $null) { try { $word.Quit() } catch {} }
}
"""
        script_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".ps1", encoding="utf-8", delete=False
            ) as handle:
                handle.write(script)
                script_path = handle.name
            run(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    script_path,
                    str(Path(source).resolve()),
                    str(Path(output).resolve()),
                    target,
                ]
            )
        finally:
            if script_path:
                Path(script_path).unlink(missing_ok=True)
        if not Path(output).is_file() or Path(output).stat().st_size == 0:
            raise ConversionFailure(f"Microsoft Word did not produce a {target[1:].upper()} file.")
        return True

    app = document = None
    try:
        app = DispatchEx("Word.Application")
        app.Visible = False
        app.DisplayAlerts = 0
        document = app.Documents.Open(
            FileName=str(Path(source).resolve()),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            Visible=False,
        )
        # wdFormatPDF = 17; wdFormatDocumentDefault (DOCX) = 16.
        document.SaveAs2(
            FileName=str(Path(output).resolve()),
            FileFormat=17 if target == ".pdf" else 16,
        )
        document.Close(False)
        document = None
        if not Path(output).is_file() or Path(output).stat().st_size == 0:
            raise ConversionFailure(f"Microsoft Word did not produce a {target[1:].upper()} file.")
        return True
    except ConversionFailure:
        raise
    except Exception as exc:
        raise ConversionFailure(
            f"Microsoft Word could not convert this file to {target[1:].upper()}."
        ) from exc
    finally:
        if document is not None:
            try:
                document.Close(False)
            except Exception:
                pass
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass


def office_convert(source, output, target, directory):
    if target == ".pdf" and Path(source).suffix.lower() == ".docx":
        if _word_convert(source, output, target):
            return
    profile = Path(directory) / f"lo-profile-{source.stem}"
    office_cli = shutil.which("libreoffice") or shutil.which("soffice")
    if not office_cli:
        raise ConversionFailure(
            "LibreOffice is required for this conversion (or install Microsoft Word on Windows)."
        )
    run(
        [
            office_cli,
            "--headless",
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--convert-to",
            target.lstrip("."),
            "--outdir",
            str(output.parent),
            str(source),
        ]
    )
    generated = output.parent / f"{source.stem}{target}"
    if not generated.exists():
        raise ConversionFailure(f"LibreOffice did not produce a {target[1:].upper()} file.")
    if generated != output:
        generated.replace(output)


def document_to_pdf(source, output, directory):
    """Convert Office files with LibreOffice and TXT/Markdown with the text renderer."""
    if source.suffix.lower() in {".txt", ".md"}:
        text_pdf(source, output)
    else:
        office_convert(source, output, ".pdf", directory)
