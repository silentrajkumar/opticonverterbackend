# OptiConverter Backend

Python API for the PDF conversion tools used by OptiConverter.

## Features

- PDF compression with Ghostscript, retaining the original when compression would increase its size.
- PDF to JPG/PNG conversion, returning all pages in a ZIP archive.
- PDF to DOCX conversion with editable text, detected native tables, and OCR-positioned text on scanned pages.
- PDF merge, page-range split, rotation, watermark, and metadata operations.
- AI mode for dropdown-selected conversion of supported image, PDF, Office, text, audio, video, and archive formats.
- Deterministic batch conversion of mixed PDF, DOCX, image, text, and Markdown uploads.

## Project structure

```text
src/
├── ai_converter.py                 # legacy import compatibility
├── app.py                          # legacy launch compatibility
├── convert_pdf_to_docx.py          # legacy import compatibility
└── opticonverterbackend/
    ├── app.py
    ├── ai_converter.py
    ├── convert_pdf_to_docx.py
    └── converters/
        ├── archive_converter.py
        ├── audio_converter.py
        ├── image_converter.py
        ├── office_converter.py
        ├── pdf_converter.py
        └── ...
```

`pyproject.toml` defines the installable package, test settings, and Ruff
configuration; `requirements.txt` supplies runtime dependencies. Root-level Python
files are reserved for project configuration and documentation. Legacy import
modules are kept under `src/` and included in the installed distribution.

## Converter modules

The installable application lives under `src/opticonverterbackend/`. Format-specific
conversion logic is separated under `src/opticonverterbackend/converters/`:

- `image_converter.py` handles raster image conversion.
- `pdf_converter.py` handles PDF rendering, compression, and editable DOCX export.
- `office_converter.py` handles Office and text documents.
- `audio_converter.py` and `video_converter.py` provide separate FFmpeg engines for each media type.
- `text_converter.py` lays out plain text and Markdown as PDF.
- `ocr_converter.py` handles OCR extraction from uploaded images.
- `archive_converter.py` handles ZIP and TAR archives.
- `router.py` validates the input/output pair and dispatches to the matching converter.
- `common.py` contains shared process helpers and conversion errors.

`opticonverterbackend.app` owns the HTTP routes. The top-level modules directly
under `src/` preserve older imports and launch commands; new application code
belongs in the `opticonverterbackend` package.

## Run locally

The recommended local setup uses Docker Compose so the API has all of its required conversion tools:

```sh
docker compose up --build -d
```

The API listens on port `8000` by default, matching the frontend's local Vite proxy. Stop it with `docker compose down`.

To run the API directly with Python, install Python 3.12+, Ghostscript, Poppler, Tesseract OCR, LibreOffice (Writer, Calc, and Impress), FFmpeg, and the HEIF system library first.

```sh
python -m venv .venv
```

Activate the virtual environment:

```powershell
.venv\Scripts\Activate.ps1
```

On macOS or Linux, use:

```sh
source .venv/bin/activate
```

Then install the package and run the API:

```sh
python -m pip install -e .
python -m opticonverterbackend
```

The API listens on port `8000` by default.

### Development and tests

Install the development tools and run the test suite and linter with:

```sh
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

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

The API listens on port `8000` by default. Start locally with `python -m opticonverterbackend`, or use
the included Docker Compose configuration. CORS is selected by the environment file:
local mode allows `http://localhost:5173`, while production mode allows
`https://opticonverter.com` and `https://www.opticonverter.com`.

For local development, create `.env.local` from `.env.example` and run:

```sh
docker compose --env-file .env.local up -d --build
curl http://127.0.0.1:8000/health
```

For production, create `.env.production` from `.env.production.example` and run:

```sh
docker compose --env-file .env.production up -d --build
```

The frontend's deployment workflow sets `SITE_URL` to `https://opticonverter.com`;
the GitHub repository URL is not a browser origin. Set the frontend repository's
`CONVERTER_API_URL` Actions variable to the public HTTPS URL of this API. You can
override `FRONTEND_ORIGIN` in either environment file with a comma-separated list
of exact browser origins.

Put the API behind HTTPS and a reverse proxy before exposing it publicly, and
configure rate limiting for production use.
