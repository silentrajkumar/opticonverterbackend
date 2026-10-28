import { createReadStream, createWriteStream } from "node:fs";
import { access, mkdtemp, readFile, readdir, rename, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { execFile as execFileCallback } from "node:child_process";
import { pipeline as streamPipeline } from "node:stream/promises";
import { Transform } from "node:stream";
import { createGunzip, gzipSync } from "node:zlib";
import { PDFDocument } from "pdf-lib";
import PDFDocumentWriter from "pdfkit";
import sharp from "sharp";
import tar from "tar-stream";
import yauzl from "yauzl";
import yazl from "yazl";
import { ConversionError } from "./errors.js";
import { recognizeImage } from "./ocr.js";

const execFile = promisify(execFileCallback);

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
export const MAX_OUTPUT_BYTES = 100 * 1024 * 1024;
export const MAX_ARCHIVE_CONTENT_BYTES = 100 * 1024 * 1024;
export const CONVERSION_TIMEOUT_MS = 120_000;
const OCR_PAGE_CONCURRENCY = 2;
const MAX_OCR_RESPONSE_BYTES = 10 * 1024 * 1024;

const IMAGE_INPUTS = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"]);
const IMAGE_OUTPUTS = new Set([".png", ".jpg", ".jpeg", ".webp"]);
const OFFICE_INPUTS = new Set([".docx", ".pptx", ".xlsx"]);
const VIDEO_INPUTS = new Set([".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"]);
const VIDEO_OUTPUTS = new Set([".mp4", ".mov", ".mkv", ".webm"]);
const AUDIO_INPUTS = new Set([".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".opus"]);
const AUDIO_OUTPUTS = new Set([".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg"]);
const ARCHIVE_INPUTS = new Set([".zip", ".tar", ".tar.gz", ".tgz"]);
const ARCHIVE_OUTPUTS = new Set([".zip", ".tar", ".tar.gz"]);
const INPUT_EXTENSIONS = new Set([
  ...IMAGE_INPUTS, ...OFFICE_INPUTS, ...VIDEO_INPUTS, ...AUDIO_INPUTS, ...ARCHIVE_INPUTS, ".pdf", ".txt",
]);

export function fileExtension(filename) {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".tar.gz")) return ".tar.gz";
  if (lower.endsWith(".tgz")) return ".tgz";
  return path.posix.extname(lower);
}

export function outputName(inputName, target) {
  const basename = inputName.replaceAll("\\", "/").split("/").pop() || "";
  let stem = basename;
  for (const extension of [...INPUT_EXTENSIONS].sort((a, b) => b.length - a.length)) {
    if (stem.toLowerCase().endsWith(extension)) {
      stem = stem.slice(0, -extension.length);
      break;
    }
  }
  const safeStem = stem.replace(/[^a-zA-Z0-9_. -]/g, "").slice(0, 100).trim().replace(/^[. ]+|[. ]+$/g, "") || "converted";
  return `${safeStem}${target}`;
}

function checkedMemberName(name) {
  if (name.includes("\\") || name.includes("\0")) return null;
  const normalized = path.posix.normalize(name);
  if (path.posix.isAbsolute(name) || normalized === "." || normalized === ".." || normalized.startsWith("../")) return null;
  if (name.split("/").some((part) => part === "." || part === "..")) return null;
  return normalized;
}

function archiveError(error) {
  if (error instanceof ConversionError) return error;
  return new ConversionError(422, `Could not read this archive: ${error.message}`);
}

async function readZip(source) {
  return new Promise((resolve, reject) => {
    yauzl.open(source, { lazyEntries: true, validateEntrySizes: true }, (openError, archive) => {
      if (openError) {
        reject(archiveError(openError));
        return;
      }

      const entries = [];
      let totalBytes = 0;
      let finished = false;
      const fail = (error) => {
        if (finished) return;
        finished = true;
        archive.close();
        reject(archiveError(error));
      };

      archive.on("error", fail);
      archive.on("end", () => {
        if (finished) return;
        finished = true;
        resolve(entries);
      });
      archive.on("entry", (entry) => {
        const name = checkedMemberName(entry.fileName);
        const mode = (entry.externalFileAttributes >>> 16) & 0o170000;
        const isDirectory = entry.fileName.endsWith("/");
        if (isDirectory || !name || mode === 0o120000) {
          archive.readEntry();
          return;
        }
        totalBytes += entry.uncompressedSize;
        if (totalBytes > MAX_ARCHIVE_CONTENT_BYTES) {
          fail(new ConversionError(413, "Expanded archive contents exceed 100 MB."));
          return;
        }
        archive.openReadStream(entry, (streamError, stream) => {
          if (streamError) {
            fail(streamError);
            return;
          }
          const chunks = [];
          let size = 0;
          stream.on("data", (chunk) => {
            size += chunk.length;
            if (size > MAX_ARCHIVE_CONTENT_BYTES) {
              stream.destroy(new ConversionError(413, "Expanded archive contents exceed 100 MB."));
              return;
            }
            chunks.push(chunk);
          });
          stream.on("error", fail);
          stream.on("end", () => {
            if (finished) return;
            entries.push([name, Buffer.concat(chunks)]);
            archive.readEntry();
          });
        });
      });
      archive.readEntry();
    });
  });
}

async function readTar(source, inputExt) {
  const extract = tar.extract();
  const entries = [];
  let totalBytes = 0;

  const reading = new Promise((resolve, reject) => {
    extract.on("entry", (header, stream, next) => {
      const size = Number(header.size) || 0;
      totalBytes += size;
      if (totalBytes > MAX_ARCHIVE_CONTENT_BYTES) {
        stream.resume();
        reject(new ConversionError(413, "Expanded archive contents exceed 100 MB."));
        extract.destroy();
        return;
      }

      const name = checkedMemberName(header.name || "");
      if (!name || !["file", "contiguous-file"].includes(header.type)) {
        stream.resume();
        stream.on("end", next);
        return;
      }

      const chunks = [];
      let actualSize = 0;
      stream.on("data", (chunk) => {
        actualSize += chunk.length;
        if (actualSize > MAX_ARCHIVE_CONTENT_BYTES) {
          stream.destroy(new ConversionError(413, "Expanded archive contents exceed 100 MB."));
          return;
        }
        chunks.push(chunk);
      });
      stream.on("error", reject);
      stream.on("end", () => {
        entries.push([name, Buffer.concat(chunks)]);
        next();
      });
    });
    extract.on("finish", resolve);
    extract.on("error", reject);
  });

  try {
    const input = createReadStream(source);
    const inputPipeline = inputExt === ".tar.gz" || inputExt === ".tgz"
      ? streamPipeline(input, createGunzip(), extract)
      : streamPipeline(input, extract);
    await Promise.all([reading, inputPipeline]);
    return entries;
  } catch (error) {
    throw archiveError(error);
  }
}

async function archiveEntries(source, inputExt) {
  return inputExt === ".zip" ? readZip(source) : readTar(source, inputExt);
}

async function writeZip(destination, entries) {
  const zip = new yazl.ZipFile();
  const chunks = [];
  const output = new Promise((resolve, reject) => {
    zip.outputStream.on("data", (chunk) => chunks.push(chunk));
    zip.outputStream.on("error", reject);
    zip.outputStream.on("end", () => resolve(Buffer.concat(chunks)));
  });
  for (const [name, contents] of entries) zip.addBuffer(contents, name);
  zip.end();
  await writeFile(destination, await output);
}

async function writeZipFiles(destination, files) {
  const zip = new yazl.ZipFile();
  let outputBytes = 0;
  const sizeLimit = new Transform({
    transform(chunk, _encoding, callback) {
      outputBytes += chunk.length;
      callback(outputBytes > MAX_OUTPUT_BYTES
        ? new ConversionError(413, "The converted file is larger than the 100 MB output limit.")
        : null, chunk);
    },
  });
  for (const [filePath, archiveName] of files) zip.addFile(filePath, archiveName);
  zip.end();
  await streamPipeline(zip.outputStream, sizeLimit, createWriteStream(destination));
}

async function writeTar(destination, entries, target) {
  const pack = tar.pack();
  const chunks = [];
  const packed = new Promise((resolve, reject) => {
    pack.on("data", (chunk) => chunks.push(chunk));
    pack.on("error", reject);
    pack.on("end", () => resolve(Buffer.concat(chunks)));
  });
  for (const [name, contents] of entries) {
    await new Promise((resolve, reject) => {
      pack.entry({ name, size: contents.length, mode: 0o644, mtime: new Date(0) }, contents, (error) => error ? reject(error) : resolve());
    });
  }
  pack.finalize();
  let archive = await packed;
  if (target === ".tar.gz") archive = gzipSync(archive, { level: 9, maxOutputLength: MAX_OUTPUT_BYTES + 1 });
  await writeFile(destination, archive);
}

async function convertImage(source, destination, target, quality) {
  try {
    const image = sharp(source, { page: 0, limitInputPixels: 40_000_000 }).rotate();
    const metadata = await image.metadata();
    if ((metadata.width || 0) * (metadata.height || 0) > 40_000_000) {
      throw new ConversionError(422, "Images over 40 megapixels can't be converted.");
    }
    if (target === ".jpg" || target === ".jpeg") {
      await image.flatten({ background: "#ffffff" }).jpeg({ quality, mozjpeg: true }).toFile(destination);
    } else if (target === ".webp") {
      await image.webp({ quality }).toFile(destination);
    } else {
      await image.png({ compressionLevel: 9 }).toFile(destination);
    }
  } catch (error) {
    if (error instanceof ConversionError) throw error;
    const detail = /\.(heic|heif)$/i.test(source)
      ? "HEIC/HEIF conversion requires a Sharp build with libheif codec support."
      : `Could not read or convert this image: ${error.message}`;
    throw new ConversionError(422, detail);
  }
}

async function convertPdf(source, destination, target) {
  if (target === ".pdf") {
    const optimizedDestination = `${destination}.optimized`;
    try {
      await runTool("gs", [
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.7",
        "-dPDFSETTINGS=/ebook",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        "-dSAFER",
        `-sOutputFile=${optimizedDestination}`,
        source,
      ]);
      const [inputInfo, optimizedInfo] = await Promise.all([stat(source), stat(optimizedDestination)]);
      await rename(
        optimizedInfo.size < inputInfo.size ? optimizedDestination : source,
        destination,
      );
    } catch (error) {
      await rm(optimizedDestination, { force: true });
      if (error.code === "ENOENT") {
        throw new ConversionError(503, "Ghostscript is required for PDF optimization.");
      }
      if (error instanceof ConversionError) throw error;
      throw new ConversionError(422, "Could not optimize this PDF. Encrypted or invalid PDFs are not supported.");
    }
    return;
  }

  const prefix = destination.slice(0, -path.extname(destination).length);
  const format = target === ".jpg" || target === ".jpeg" ? "-jpeg" : "-png";
  const extension = format === "-jpeg" ? ".jpg" : ".png";
  try {
    await runTool("pdftoppm", ["-scale-to", "6000", format, source, prefix]);
    const prefixName = path.basename(prefix);
    const pages = (await readdir(path.dirname(destination)))
      .map((filename) => {
        if (!filename.startsWith(`${prefixName}-`) || !filename.endsWith(extension)) return null;
        const page = Number(filename.slice(prefixName.length + 1, -extension.length));
        return Number.isInteger(page) && page > 0 ? { filename, page } : null;
      })
      .filter(Boolean)
      .sort((left, right) => left.page - right.page);
    if (!pages.length) throw new ConversionError(422, "Could not render any pages from this PDF.");
    const archiveBase = path.basename(destination, path.extname(destination));
    await writeZipFiles(destination, pages.map(({ filename, page }) => [
      path.join(path.dirname(destination), filename),
      `${archiveBase}-${page}${extension}`,
    ]));
  } catch (error) {
    if (error.code === "ENOENT") throw new ConversionError(503, "Poppler (pdftoppm) is required for PDF-to-image conversion.");
    if (error instanceof ConversionError) throw error;
    throw new ConversionError(422, "Could not render every page of this PDF. Encrypted or invalid PDFs are not supported.");
  }
}

function escapeXml(text) {
  return text
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

async function createDocx(text) {
  const paragraphs = text.split(/\r?\n/).map((line) => (
    `<w:p><w:r><w:t xml:space="preserve">${escapeXml(line)}</w:t></w:r></w:p>`
  ));
  const document = [
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>',
    ...paragraphs,
    '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>',
    '</w:body></w:document>',
  ].join("");
  const relationships = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>';
  const contentTypes = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>';
  const chunks = [];
  const zip = new yazl.ZipFile();
  const output = new Promise((resolve, reject) => {
    zip.outputStream.on("data", (chunk) => chunks.push(chunk));
    zip.outputStream.on("error", reject);
    zip.outputStream.on("end", () => resolve(Buffer.concat(chunks)));
  });
  zip.addBuffer(Buffer.from(contentTypes), "[Content_Types].xml");
  zip.addBuffer(Buffer.from(relationships), "_rels/.rels");
  zip.addBuffer(Buffer.from(document), "word/document.xml");
  zip.end();
  return output;
}

async function convertPdfToWord(source, destination, recognize = recognizeImage) {
  const deadline = Date.now() + CONVERSION_TIMEOUT_MS;
  let text;
  try {
    ({ stdout: text } = await execFile("pdftotext", ["-layout", source, "-"], {
      timeout: remainingTimeout(deadline),
      maxBuffer: 10 * 1024 * 1024,
      windowsHide: true,
    }));
  } catch (error) {
    if (error instanceof ConversionError) throw error;
    if (error.code === "ENOENT") {
      throw new ConversionError(503, "Poppler (pdftotext) is required for PDF-to-Word conversion.");
    }
    if (error.code === "ETIMEDOUT") {
      throw new ConversionError(422, "The conversion exceeded the 2-minute limit.");
    }
    throw new ConversionError(422, "Could not read this PDF. Encrypted or invalid PDFs are not supported.");
  }
  const extractedPages = text.split("\f").map((pageText) => pageText.trim());
  while (extractedPages.at(-1) === "") extractedPages.pop();
  if (!extractedPages.some(Boolean)) {
    text = await recognizeScannedPdf(source, destination, deadline, recognize);
  } else {
    const pageCount = await getPdfPageCount(source, deadline);
    while (extractedPages.length < pageCount) extractedPages.push("");
    if (extractedPages.length > pageCount) extractedPages.length = pageCount;
    const missingPages = extractedPages.flatMap((pageText, index) => pageText ? [] : [index + 1]);
    if (missingPages.length) {
      if (process.env.OCR_ENDPOINT_URL) {
        text = await recognizeWithEndpoint(source, deadline);
      } else {
        const workingDirectory = await mkdtemp(path.join(path.dirname(destination), "opticonverter-ocr-"));
        try {
          const scannedPages = await recognizePdfPages(source, workingDirectory, missingPages, deadline, recognize);
          for (const [pageNumber, pageText] of scannedPages) extractedPages[pageNumber - 1] = pageText;
        } finally {
          await rm(workingDirectory, { recursive: true, force: true });
        }
        text = extractedPages.join("\n\n").trim();
      }
    } else {
      text = extractedPages.join("\n\n").trim();
    }
  }
  await writeFile(destination, await createDocx(text));
}

function remainingTimeout(deadline) {
  const timeout = deadline - Date.now();
  if (timeout <= 0) throw new ConversionError(422, "The conversion exceeded the 2-minute limit.");
  return timeout;
}

async function readLimitedResponse(response) {
  const reader = response.body?.getReader();
  if (!reader) throw new ConversionError(502, "The OCR endpoint returned an empty response.");
  const chunks = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > MAX_OCR_RESPONSE_BYTES) {
        await reader.cancel();
        throw new ConversionError(502, "The OCR endpoint response exceeded the 10 MB limit.");
      }
      chunks.push(Buffer.from(value));
    }
  } finally {
    reader.releaseLock();
  }
  return Buffer.concat(chunks).toString("utf8");
}

async function recognizeWithEndpoint(source, deadline) {
  let endpoint;
  try {
    endpoint = new URL(process.env.OCR_ENDPOINT_URL);
  } catch {
    throw new ConversionError(500, "OCR_ENDPOINT_URL must be a valid HTTP or HTTPS URL.");
  }
  if (!["http:", "https:"].includes(endpoint.protocol) || endpoint.username || endpoint.password || endpoint.hash) {
    throw new ConversionError(500, "OCR_ENDPOINT_URL must use HTTP or HTTPS and must not contain credentials or a fragment.");
  }

  const headers = { "Content-Type": "application/pdf", Accept: "application/json" };
  if (process.env.OCR_ENDPOINT_TOKEN) {
    headers.Authorization = `Bearer ${process.env.OCR_ENDPOINT_TOKEN}`;
  }
  let response;
  try {
    response = await fetch(endpoint, {
      method: "POST",
      headers,
      body: await readFile(source),
      redirect: "error",
      signal: AbortSignal.timeout(remainingTimeout(deadline)),
    });
  } catch (error) {
    if (error instanceof ConversionError) throw error;
    if (error.name === "TimeoutError" || error.name === "AbortError") {
      throw new ConversionError(504, "The OCR endpoint exceeded the 2-minute conversion limit.");
    }
    throw new ConversionError(502, "Could not connect to the configured OCR endpoint.");
  }
  if (!response.ok) {
    await response.body?.cancel();
    throw new ConversionError(502, `The OCR endpoint returned HTTP ${response.status}.`);
  }

  let result;
  try {
    result = JSON.parse(await readLimitedResponse(response));
  } catch (error) {
    if (error instanceof ConversionError) throw error;
    throw new ConversionError(502, "The OCR endpoint must return JSON containing a text field.");
  }
  if (typeof result?.text !== "string" || !result.text.trim()) {
    throw new ConversionError(422, "The OCR endpoint did not recognize readable text in this scanned PDF.");
  }
  return result.text.trim();
}

async function getPdfPageCount(source, deadline) {
  let pageCount;
  try {
    const { stdout } = await execFile("pdfinfo", [source], {
      timeout: remainingTimeout(deadline),
      maxBuffer: 1024 * 1024,
      windowsHide: true,
    });
    pageCount = Number(stdout.match(/^Pages:\s+(\d+)/m)?.[1]);
  } catch (error) {
    if (error instanceof ConversionError) throw error;
    if (error.code === "ENOENT") {
      throw new ConversionError(503, "Poppler (pdfinfo) is required to inspect scanned PDFs.");
    }
    if (error.code === "ETIMEDOUT") {
      throw new ConversionError(422, "The conversion exceeded the 2-minute limit.");
    }
    throw new ConversionError(422, "Could not inspect the pages in this PDF.");
  }
  if (!Number.isInteger(pageCount) || pageCount < 1) {
    throw new ConversionError(422, "Could not determine the number of PDF pages for OCR.");
  }
  return pageCount;
}

async function recognizePdfPages(source, workingDirectory, pageNumbers, deadline, recognize = recognizeImage) {
  const pageCount = await getPdfPageCount(source, deadline);
  const prefix = path.join(workingDirectory, "page");
  try {
    await runTool("pdftoppm", [
      "-f", "1", "-l", String(pageCount), "-r", "180", "-scale-to", "2400", "-png", source, prefix,
    ], remainingTimeout(deadline));
  } catch (error) {
    if (error.code === "ENOENT") {
      throw new ConversionError(503, "Poppler (pdftoppm) is required to render scanned PDF pages for OCR.");
    }
    if (error instanceof ConversionError) throw error;
    throw new ConversionError(422, "Could not render this PDF for OCR. Encrypted or invalid PDFs are not supported.");
  }

  const pageImages = (await readdir(workingDirectory))
    .filter((filename) => /^page-\d+\.png$/.test(filename))
    .sort((left, right) => Number(left.match(/\d+/)?.[0]) - Number(right.match(/\d+/)?.[0]));
  if (pageImages.length !== pageCount) {
    throw new ConversionError(422, "Could not render every page of this PDF for OCR.");
  }

  const selectedPages = pageNumbers || pageImages.map((_imageName, index) => index + 1);
  const recognizedPages = new Map();
  for (let batchStart = 0; batchStart < selectedPages.length; batchStart += OCR_PAGE_CONCURRENCY) {
    const batch = selectedPages.slice(batchStart, batchStart + OCR_PAGE_CONCURRENCY);
    const batchText = await Promise.all(batch.map(async (pageNumber) => {
      const index = pageNumber - 1;
      const imageName = pageImages[index];
      const imagePath = path.join(workingDirectory, imageName);
      try {
        const { text } = await recognize(await readFile(imagePath));
        remainingTimeout(deadline);
        return text.trim();
      } catch {
        throw new ConversionError(422, `OCR could not recognize PDF page ${index + 1}.`);
      }
    }));
    for (const [batchIndex, pageText] of batchText.entries()) {
      recognizedPages.set(batch[batchIndex], pageText);
    }
  }
  return recognizedPages;
}

export async function recognizePdfText(source, workingDirectory, recognize = recognizeImage) {
  const deadline = Date.now() + CONVERSION_TIMEOUT_MS;
  const recognizedPages = await recognizePdfPages(source, workingDirectory, undefined, deadline, recognize);
  const text = [...recognizedPages.values()].join("\n\n").trim();
  if (!text) throw new ConversionError(422, "OCR could not recognize readable text in this scanned PDF.");
  return text;
}

async function recognizeScannedPdf(source, destination, deadline, recognize = recognizeImage) {
  if (process.env.OCR_ENDPOINT_URL) return recognizeWithEndpoint(source, deadline);
  const workingDirectory = await mkdtemp(path.join(path.dirname(destination), "opticonverter-ocr-"));
  try {
    const recognizedPages = await recognizePdfPages(source, workingDirectory, undefined, deadline, recognize);
    const text = [...recognizedPages.values()].join("\n\n").trim();
    if (!text) throw new ConversionError(422, "OCR could not recognize readable text in this scanned PDF.");
    return text;
  } finally {
    await rm(workingDirectory, { recursive: true, force: true });
  }
}

async function convertOffice(source, destination, inputExt) {
  try {
    await runTool("libreoffice", [
      "--headless",
      `-env:UserInstallation=file://${path.join(path.dirname(destination), "lo-profile")}`,
      "--convert-to", "pdf",
      "--outdir", path.dirname(destination),
      source,
    ]);
  } catch (error) {
    if (error instanceof ConversionError) throw error;
    if (error.code === "ENOENT") throw new ConversionError(503, "LibreOffice is required for Office document conversion.");
    throw new ConversionError(422, "LibreOffice could not convert this document.");
  }
  const generated = path.join(path.dirname(destination), `${path.basename(source, inputExt)}.pdf`);
  try {
    await access(generated);
    if (generated !== destination) await rename(generated, destination);
  } catch {
    throw new ConversionError(422, "LibreOffice did not produce a PDF for this document.");
  }
}

async function convertText(source, destination) {
  let text;
  try {
    text = (await readFile(source, "utf8")).replace(/^\uFEFF/, "");
  } catch (error) {
    throw new ConversionError(422, `Could not read this text file: ${error.message}`);
  }
  if ([...text].some((character) => character.codePointAt(0) > 255)) {
    throw new ConversionError(422, "TXT-to-PDF supports Latin-1 text only.");
  }
  await new Promise((resolve, reject) => {
    const pdf = new PDFDocumentWriter({ size: "LETTER", margin: 48 });
    const stream = createWriteStream(destination);
    pdf.pipe(stream);
    pdf.font("Helvetica").fontSize(10);
    for (const line of text.split(/\r?\n/)) {
      for (let offset = 0; offset < (line.length || 1); offset += 95) {
        const chunk = line.slice(offset, offset + 95);
        if (pdf.y < 48) pdf.addPage();
        pdf.text(chunk, { lineBreak: false });
        pdf.moveDown(0.4);
      }
    }
    pdf.end();
    stream.on("finish", resolve);
    stream.on("error", reject);
  });
}

async function convertMedia(source, destination, target, inputExt) {
  const args = [
    "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", source,
  ];
  if (AUDIO_OUTPUTS.has(target)) args.push("-vn");
  else if (AUDIO_INPUTS.has(inputExt)) throw new ConversionError(422, "Audio files cannot be converted to a video format.");
  if (target === ".mp4") args.push("-movflags", "+faststart");
  args.push(destination);
  try {
    await runTool("ffmpeg", args);
  } catch (error) {
    if (error instanceof ConversionError) throw error;
    if (error.code === "ENOENT") throw new ConversionError(503, "FFmpeg is required for audio and video conversion.");
    throw new ConversionError(422, "FFmpeg could not convert this media file.");
  }
}

async function convertArchive(source, destination, inputExt, target) {
  if (target === inputExt || (inputExt === ".tgz" && target === ".tar.gz")) {
    throw new ConversionError(422, "Choose a different output archive format.");
  }
  const entries = await archiveEntries(source, inputExt);
  if (!entries.length) throw new ConversionError(422, "This archive contains no convertible files.");
  if (target === ".zip") await writeZip(destination, entries);
  else await writeTar(destination, entries, target);
}

export async function runTool(name, args, timeout = CONVERSION_TIMEOUT_MS) {
  try {
    await execFile(name, args, {
      timeout,
      maxBuffer: 2 * 1024 * 1024,
      windowsHide: true,
    });
  } catch (error) {
    if (error.code === "ETIMEDOUT") throw new ConversionError(422, "The conversion exceeded the 2-minute limit.");
    throw error;
  }
}

export async function runConversion(source, destination, inputName, target, quality, { recognize = recognizeImage } = {}) {
  const inputExt = fileExtension(inputName);
  if (IMAGE_INPUTS.has(inputExt) && IMAGE_OUTPUTS.has(target)) {
    await convertImage(source, destination, target, quality);
  } else if (inputExt === ".pdf" && target === ".docx") {
    await convertPdfToWord(source, destination, recognize);
  } else if (inputExt === ".pdf" && new Set([".pdf", ".jpg", ".jpeg", ".png"]).has(target)) {
    await convertPdf(source, destination, target);
  } else if (OFFICE_INPUTS.has(inputExt) && target === ".pdf") {
    await convertOffice(source, destination, inputExt);
  } else if (inputExt === ".txt" && target === ".pdf") {
    await convertText(source, destination);
  } else if ((VIDEO_INPUTS.has(inputExt) || AUDIO_INPUTS.has(inputExt)) && (VIDEO_OUTPUTS.has(target) || AUDIO_OUTPUTS.has(target))) {
    if (VIDEO_INPUTS.has(inputExt) && AUDIO_OUTPUTS.has(target)) {
      throw new ConversionError(422, "Video-to-audio extraction is not supported yet.");
    }
    await convertMedia(source, destination, target, inputExt);
  } else if (ARCHIVE_INPUTS.has(inputExt) && ARCHIVE_OUTPUTS.has(target)) {
    await convertArchive(source, destination, inputExt, target);
  } else {
    throw new ConversionError(422, `Conversion from ${inputExt || "this file type"} to ${target} is not supported.`);
  }
}

export async function getFormats() {
  const tools = {};
  for (const [tool, args] of [["ffmpeg", ["-version"]], ["libreoffice", ["--version"]], ["pdftoppm", ["-v"]], ["gs", ["--version"]]]) {
    try {
      await execFile(tool, args, { timeout: 1000, maxBuffer: 2048 });
      tools[tool] = true;
    } catch (error) {
      tools[tool] = error.code !== "ENOENT";
    }
  }
  return {
    max_upload_bytes: MAX_UPLOAD_BYTES,
    native_tools: { ...tools, "tesseract.js": true },
    ocr_engine: "tesseract.js",
    ocr_endpoint_configured: Boolean(process.env.OCR_ENDPOINT_URL),
    conversions: {
      images: { input: [...IMAGE_INPUTS].sort(), output: [...IMAGE_OUTPUTS].sort(), processing: "browser or Node" },
      pdf: { input: [".pdf"], output: [".jpg", ".png", ".pdf", ".docx"], processing: "Node, Poppler, and Tesseract.js OCR for scanned PDFs" },
      documents: { input: [...OFFICE_INPUTS, ".txt"].sort(), output: [".pdf"], processing: "Node and LibreOffice for Office documents" },
      video: { input: [...VIDEO_INPUTS].sort(), output: [...VIDEO_OUTPUTS].sort(), processing: "Node and FFmpeg" },
      audio: { input: [...AUDIO_INPUTS].sort(), output: [...AUDIO_OUTPUTS].sort(), processing: "Node and FFmpeg" },
      archives: { input: [...ARCHIVE_INPUTS].sort(), output: [...ARCHIVE_OUTPUTS].sort(), processing: "Node" },
    },
  };
}

export async function removeTemporaryDirectory(directory) {
  const { rm } = await import("node:fs/promises");
  await rm(directory, { recursive: true, force: true });
}
