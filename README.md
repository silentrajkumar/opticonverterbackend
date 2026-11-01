# OptiConverter Backend

Python API for the PDF conversion tools used by OptiConverter.

## Features

- PDF compression with Ghostscript, retaining the original when compression would increase its size.
- PDF to JPG/PNG conversion, returning all pages in a ZIP archive.
- PDF to DOCX conversion with editable text, detected native tables, and OCR-positioned text on scanned pages.
- PDF merge, page-range split, rotation, watermark, and metadata operations.
- AI mode for dropdown-selected conversion of supported image, PDF, Office, text, audio, video, and archive formats.
- Deterministic batch conversion of mixed PDF, DOCX, image, text, and Markdown uploads.

## Converter modules

Format-specific conversion logic is separated under `converters/`:

- `image_converter.py` handles raster image conversion.
- `pdf_converter.py` handles PDF rendering, compression, and editable DOCX export.
- `office_converter.py` handles Office and text documents.
- `audio_converter.py` and `video_converter.py` provide separate FFmpeg engines for each media type.
- `media_converter.py` remains as a compatibility facade for older imports.
- `text_converter.py` lays out plain text and Markdown as PDF.
- `ocr_converter.py` handles OCR extraction from uploaded images.
- `archive_converter.py` handles ZIP and TAR archives.
- `router.py` validates the input/output pair and dispatches to the matching converter.
- `common.py` contains shared process helpers and conversion errors.

`app.py` owns the HTTP routes. `ai_converter.py` remains as a small compatibility
module for existing imports; new converter code belongs in `converters/`.

## Run locally

The recommended local setup uses Docker Compose so the API has all of its required conversion tools:

```sh
docker compose up --build -d
```

The API listens on port `8000` by default, matching the frontend's local Vite proxy. Stop it with `docker compose down`.

To run the API directly with Python, install Python 3.12+, Ghostscript, Poppler, Tesseract OCR, LibreOffice (Writer, Calc, and Impress), FFmpeg, and the HEIF system library first.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The API listens on port `8000` by default.

### API

- `GET /health` returns `{ "status": "ok" }`.
- `GET /formats` returns supported conversion formats.
- `POST /convert?format=<extension>` accepts one multipart `file` and supports the same dropdown-selected single-file conversions as AI mode.
- `POST /ai-convert?format=<extension>` accepts one multipart `file` and converts it to the selected dropdown format. Optional query parameters are `quality`, `compression`, and `compress=any`. It supports image, PDF, Office, text, media, and archive conversion pairs implemented by the service.
- `POST /api/ocr` accepts one multipart image `file` and returns recognized English text as JSON. Images are limited to 10 MB and 40 megapixels.
- `POST /ai/convert?format=.jpg|.png|.docx|.pdf` accepts up to 20 mixed files in the
  `files` field and returns one ZIP containing the converted result for each file.
  `/convert/batch` is an alias for this endpoint. Supported inputs are PDF, DOCX,
  PNG/JPG, TXT, and Markdown. This mode is deterministic and does not call an
  external AI provider.
- `POST /pdf/merge` accepts multiple PDF fields named `files`.
- `POST /pdf/split` accepts `file`, `start`, and `end` form fields.
- `POST /pdf/rotate` accepts `file` and `degrees` form fields.
- `POST /pdf/watermark` accepts `file` and `text` form fields.
- `POST /pdf/metadata` accepts `file` and metadata form fields.

Uploads are limited to 50 MB, generated files to 100 MB, and conversions to two minutes. Temporary files are removed after each request. Set `FRONTEND_ORIGIN` to a comma-separated list of exact browser origins for CORS.

## Docker

The API listens on port `8000` by default. Start locally with `python app.py`, or use
the included Docker Compose configuration.

For a persistent service:

```sh
docker compose up -d --build
curl http://127.0.0.1:8000/health
```

Put the API behind HTTPS and a reverse proxy before exposing it publicly. Configure rate limiting and an exact `FRONTEND_ORIGIN` for production use.
