FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ffmpeg \
      ghostscript \
      libreoffice-writer \
      libreoffice-calc \
      libreoffice-impress \
      poppler-utils \
      libheif1 \
      tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "2", "--timeout", "120", "app:app"]
