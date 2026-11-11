"""Compatibility imports for the former top-level PDF-to-DOCX converter."""

from opticonverterbackend.convert_pdf_to_docx import convert, ocr_tsv_to_text

__all__ = ["convert", "ocr_tsv_to_text"]


if __name__ == "__main__":
    import sys

    convert(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
