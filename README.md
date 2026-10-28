# OptiConverter Backend

Python API for the PDF conversion tools used by OptiConverter.

## Features

- PDF compression with Ghostscript, retaining the original when compression would increase its size.
- PDF to JPG/PNG conversion, returning all pages in a ZIP archive.
- PDF to DOCX conversion using text extraction and OCR for scanned pages.
- PDF merge, page-range split, rotation, watermark, and metadata operations.

## Run locally

Requirements: Python 3.12+, Ghostscript, Poppler, and Tesseract OCR.

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
- `POST /convert?format=.jpg|.png|.docx|.pdf` accepts one multipart file field named `file`.
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
