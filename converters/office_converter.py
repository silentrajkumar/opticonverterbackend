"""Office document and plain-text conversions."""

from pathlib import Path

from .common import ConversionFailure, run, tool
from .text_converter import text_pdf


OFFICE_INPUTS = {".docx", ".pptx", ".xlsx", ".xls", ".csv"}
OFFICE_OUTPUTS = {".pdf", ".csv", ".xlsx", ".xls"}


def office_convert(source, output, target, directory):
    profile = Path(directory) / f"lo-profile-{source.stem}"
    run([tool("libreoffice"), "--headless", f"-env:UserInstallation=file://{profile}",
         "--convert-to", target.lstrip("."), "--outdir", str(output.parent), str(source)])
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
