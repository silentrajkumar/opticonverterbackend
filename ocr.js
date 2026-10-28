import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { createScheduler, createWorker, OEM } from "tesseract.js";
import sharp from "sharp";
import { ConversionError } from "./errors.js";

const require = createRequire(import.meta.url);
const englishData = require("@tesseract.js-data/eng");
const OCR_WORKER_COUNT = 2;
const MAX_OCR_PIXELS = 40_000_000;
let schedulerPromise;

async function createOcrScheduler() {
  const scheduler = createScheduler();
  try {
    for (let index = 0; index < OCR_WORKER_COUNT; index += 1) {
      const worker = await createWorker("eng", OEM.LSTM_ONLY, {
        langPath: englishData.langPath,
        gzip: englishData.gzip,
        cachePath: path.join(os.tmpdir(), "opticonverter-tesseract"),
        cacheMethod: "none",
      });
      scheduler.addWorker(worker);
    }
    return scheduler;
  } catch (error) {
    await scheduler.terminate();
    throw new ConversionError(503, `Could not initialize local OCR: ${error.message}`);
  }
}

async function getOcrScheduler() {
  if (!schedulerPromise) {
    schedulerPromise = createOcrScheduler().catch((error) => {
      schedulerPromise = undefined;
      throw error;
    });
  }
  return schedulerPromise;
}

export async function recognizeImage(imageBuffer) {
  let normalizedImage;
  try {
    const image = sharp(imageBuffer, { limitInputPixels: MAX_OCR_PIXELS }).rotate();
    const metadata = await image.metadata();
    if (!metadata.width || !metadata.height || metadata.width * metadata.height > MAX_OCR_PIXELS) {
      throw new ConversionError(413, "OCR images must be 40 megapixels or smaller.");
    }
    normalizedImage = await image.png().toBuffer();
  } catch (error) {
    if (error instanceof ConversionError) throw error;
    throw new ConversionError(422, "The uploaded image is invalid or could not be prepared for OCR.");
  }

  try {
    const scheduler = await getOcrScheduler();
    const { data } = await scheduler.addJob("recognize", normalizedImage);
    const text = data.text.trim();
    if (!text) throw new ConversionError(422, "OCR did not recognize readable text in this image.");
    return { text, confidence: data.confidence };
  } catch (error) {
    if (error instanceof ConversionError) throw error;
    throw new ConversionError(422, `OCR failed to process this image: ${error.message}`);
  }
}

export async function closeOcrScheduler() {
  const pendingScheduler = schedulerPromise;
  schedulerPromise = undefined;
  if (!pendingScheduler) return;
  const scheduler = await pendingScheduler.catch(() => undefined);
  await scheduler?.terminate();
}
