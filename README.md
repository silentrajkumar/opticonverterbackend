# OptiConverter Backend

Node.js API for PDF and document conversion used by OptiConverter.

## Features

- PDF compression with Ghostscript, retaining the original when compression would increase its size.
- PDF to JPG/PNG conversion, returning all pages in a ZIP archive.
- PDF to DOCX conversion using text extraction and OCR for scanned pages.
- DOCX, PPTX, and XLSX to PDF conversion through LibreOffice.
- Image, audio, video, text, and archive conversions implemented by the shared OptiConverter conversion engine.
- Image OCR at `POST /api/ocr`.

## Run locally

Requirements: Node.js 20+, FFmpeg, Ghostscript, LibreOffice, and Poppler.

```sh
npm ci
FRONTEND_ORIGIN=http://localhost:5173 npm start
```

The API listens on port `8000` by default.

### API

- `GET /health` returns `{ "status": "ok" }`.
- `GET /formats` returns supported conversion formats.
- `POST /convert?format=.pdf` accepts one multipart file field named `file`.
- `POST /api/ocr` accepts one image multipart file field named `file`.

Uploads are limited to 50 MB, generated files to 100 MB, and conversions to two minutes. Temporary files are removed after each request. Set `FRONTEND_ORIGIN` to a comma-separated list of exact browser origins for CORS.

## Docker

```sh
docker build -t opticonverter-api .
docker run --rm -p 8000:8000 \
  -e FRONTEND_ORIGIN=http://localhost:5173 \
  opticonverter-api
```

For a persistent service:

```sh
docker compose up -d --build
curl http://127.0.0.1:8000/health
```

Put the API behind HTTPS and a reverse proxy before exposing it publicly. Configure rate limiting and an exact `FRONTEND_ORIGIN` for production use.

## Tests

```sh
npm test
```