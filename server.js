import { createHash, timingSafeEqual } from "node:crypto";
import { mkdir, mkdtemp, rename, rm, stat, writeFile } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";
import multer from "multer";
import { ConversionError } from "./errors.js";
import { logError } from "./errorLogger.js";
import { closeOcrScheduler, recognizeImage } from "./ocr.js";
import {
  CONVERSION_TIMEOUT_MS,
  MAX_OUTPUT_BYTES,
  MAX_UPLOAD_BYTES,
  fileExtension,
  getFormats,
  outputName,
  recognizePdfText,
  runConversion,
} from "./converters.js";

const app = express();
const upload = multer({
  dest: os.tmpdir(),
  limits: { fileSize: MAX_UPLOAD_BYTES, files: 1 },
});
const imageOcrUpload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 10 * 1024 * 1024, files: 1 },
  fileFilter: (_request, file, callback) => {
    if (!/^image\/(png|jpe?g|webp|bmp|tiff)$/.test(file.mimetype)) {
      callback(new ConversionError(415, "Upload a PNG, JPG, WebP, BMP, or TIFF image."));
      return;
    }
    callback(null, true);
  },
});

const allowedOrigins = new Set(
  (process.env.FRONTEND_ORIGIN || "http://localhost:5173")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean),
);

app.disable("x-powered-by");
app.use((request, response, next) => {
  const origin = request.get("Origin");
  if (origin && allowedOrigins.has(origin)) {
    response.set("Access-Control-Allow-Origin", origin);
    response.set("Vary", "Origin");
    response.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    response.set("Access-Control-Allow-Headers", "Authorization, Content-Type");
  }
  if (request.method === "OPTIONS") return response.sendStatus(204);
  next();
});

app.get("/health", (_request, response) => response.json({ status: "ok" }));
app.get("/formats", async (_request, response, next) => {
  try {
    response.json(await getFormats());
  } catch (error) {
    next(error);
  }
});

app.post("/api/ocr", imageOcrUpload.single("file"), async (request, response, next) => {
  if (!request.file) {
    await sendRequestError(request, response, 400, "Upload a PNG, JPG, WebP, BMP, or TIFF image.");
    return;
  }
  try {
    const result = await recognizeImage(request.file.buffer);
    if (Buffer.byteLength(result.text, "utf8") > 10 * 1024 * 1024) {
      throw new ConversionError(413, "OCR text exceeds the 10 MB response limit.");
    }
    response.json(result);
  } catch (error) {
    next(error);
  }
});

async function sendRequestError(request, response, statusCode, detail) {
  await logError(new Error(detail), { request, statusCode });
  response.status(statusCode).json({ detail });
}

async function authenticateOcrRequest(request, response, next) {
  const expectedToken = process.env.OCR_ENDPOINT_TOKEN;
  if (!expectedToken) {
    await sendRequestError(request, response, 503, "OCR_ENDPOINT_TOKEN must be configured to enable the OCR endpoint.");
    return;
  }
  const providedToken = request.get("Authorization")?.match(/^Bearer (.+)$/)?.[1] || "";
  const expectedDigest = createHash("sha256").update(expectedToken).digest();
  const providedDigest = createHash("sha256").update(providedToken).digest();
  if (!providedToken || !timingSafeEqual(providedDigest, expectedDigest)) {
    await sendRequestError(request, response, 401, "A valid bearer token is required.");
    return;
  }
  next();
}

app.post("/ocr", authenticateOcrRequest, express.raw({ type: "application/pdf", limit: MAX_UPLOAD_BYTES }), async (request, response, next) => {
  if (!Buffer.isBuffer(request.body) || request.body.length === 0) {
    await sendRequestError(request, response, 400, "Send a non-empty PDF with Content-Type application/pdf.");
    return;
  }

  let jobDirectory;
  try {
    jobDirectory = await mkdtemp(path.join(os.tmpdir(), "opticonverter-ocr-api-"));
    const source = path.join(jobDirectory, "input.pdf");
    const workingDirectory = path.join(jobDirectory, "pages");
    await writeFile(source, request.body);
    await mkdir(workingDirectory);
    const text = await recognizePdfText(source, workingDirectory);
    if (Buffer.byteLength(text, "utf8") > 10 * 1024 * 1024) {
      throw new ConversionError(413, "OCR text exceeds the 10 MB response limit.");
    }
    response.json({ text });
  } catch (error) {
    next(error);
  } finally {
    if (jobDirectory) {
      try {
        await rm(jobDirectory, { recursive: true, force: true });
      } catch (error) {
        await logError(error, { request, statusCode: 500 });
      }
    }
  }
});

app.post("/convert", upload.single("file"), async (request, response, next) => {
  let jobDirectory;
  let cleaned = false;
  const clean = async () => {
    if (cleaned) return;
    cleaned = true;
    try {
      if (request.file?.path) await rm(request.file.path, { force: true });
      if (jobDirectory) await rm(jobDirectory, { recursive: true, force: true });
    } catch (error) {
      await logError(error, { request, statusCode: 500 });
    }
  };

  try {
    const file = request.file;
    const target = String(request.query.format || "").toLowerCase();
    const quality = Number(request.query.quality || 85);
    const supportedTargets = new Set([
      ".png", ".jpg", ".jpeg", ".webp", ".pdf",
      ".docx",
      ".mp4", ".mov", ".mkv", ".webm",
      ".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg",
      ".zip", ".tar", ".tar.gz",
    ]);
    if (!file || !target || !supportedTargets.has(target)) {
      throw new ConversionError(400, "Choose a file and a supported output format.");
    }
    if (quality < 30 || quality > 100 || !Number.isInteger(quality)) {
      throw new ConversionError(400, "Image quality must be an integer between 30 and 100.");
    }
    if (file.size === 0) throw new ConversionError(400, "The uploaded file is empty.");

    const inputName = (file.originalname || "").replaceAll("\\", "/").split("/").pop() || "";
    const inputExt = fileExtension(inputName);
    if (!inputName || !inputExt) {
      throw new ConversionError(400, "The uploaded file needs a supported file extension.");
    }

    jobDirectory = await mkdtemp(path.join(os.tmpdir(), "opticonverter-"));
    const source = path.join(jobDirectory, `input${inputExt}`);
    await rename(file.path, source);
    request.file.path = "";
    const imageArchive = inputExt === ".pdf" && [".jpg", ".jpeg", ".png"].includes(target);
    const destination = path.join(jobDirectory, outputName(inputName, imageArchive ? ".zip" : target));
    await runConversion(source, destination, inputName, target, quality);
    const outputInfo = await stat(destination);
    if (outputInfo.size === 0) throw new ConversionError(422, "The conversion did not produce an output file.");
    if (outputInfo.size > MAX_OUTPUT_BYTES) {
      throw new ConversionError(413, "The converted file is larger than the 100 MB output limit.");
    }

    const cleanupOnResponse = () => void clean();
    response.once("close", cleanupOnResponse);
    response.once("finish", cleanupOnResponse);
    response.download(destination, path.basename(destination), (error) => {
      if (error && !response.headersSent) next(error);
      cleanupOnResponse();
    });
  } catch (error) {
    await clean();
    next(error);
  }
});

app.use(async (error, request, response, _next) => {
  if (error.type === "entity.too.large") {
    await logError(error, { request, statusCode: 413 });
    response.status(413).json({ detail: "Files must be 50 MB or smaller." });
    return;
  }
  if (error instanceof multer.MulterError) {
    const status = error.code === "LIMIT_FILE_SIZE" ? 413 : 400;
    await logError(error, { request, statusCode: status });
    response.status(status).json({
      detail: status === 413
        ? request.path === "/api/ocr" ? "OCR images must be 10 MB or smaller." : "Files must be 50 MB or smaller."
        : "Upload one file at a time.",
    });
    return;
  }
  if (error instanceof ConversionError) {
    await logError(error, { request, statusCode: error.status });
    response.status(error.status).json({ detail: error.message });
    return;
  }
  await logError(error, { request, statusCode: 500 });
  response.status(500).json({ detail: "An unexpected conversion error occurred." });
});

const server = http.createServer(app);
server.requestTimeout = CONVERSION_TIMEOUT_MS + 30_000;

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT || 8000);
  server.listen(port, "0.0.0.0", () => console.log(`OptiConverter API listening on ${port}`));
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, () => {
      server.close(async () => {
        await closeOcrScheduler();
        process.exit(0);
      });
    });
  }
}

export { app, server };
