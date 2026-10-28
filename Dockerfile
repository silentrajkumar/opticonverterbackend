FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ffmpeg \
      ghostscript \
      libreoffice-calc \
      libreoffice-impress \
      libreoffice-writer \
      poppler-utils \
      libheif1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY --chown=node:node . .

USER node
EXPOSE 8000
CMD ["node", "server.js"]
