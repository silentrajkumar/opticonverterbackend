import assert from "node:assert/strict";
import { chmod, mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import os from "node:os";
import path from "node:path";
import test, { after } from "node:test";
import { unzipSync } from "fflate";
import sharp from "sharp";
import tar from "tar-stream";
import { app } from "../server.js";
import { ConversionError } from "../errors.js";
import { closeOcrScheduler } from "../ocr.js";
import { outputName, runConversion } from "../converters.js";

after(async () => {
  await closeOcrScheduler();
});

async function withTempDirectory(callback) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "opticonverter-test-"));
  try {
    await callback(directory);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

async function createOcrImage(text) {
  const escapedText = text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
  const svg = `<svg width="1800" height="180" xmlns="http://www.w3.org/2000/svg"><rect width="100%" height="100%" fill="white"/><text x="30" y="115" font-family="sans-serif" font-size="56" font-weight="bold" fill="black">${escapedText}</text></svg>`;
  return sharp(Buffer.from(svg)).png().toBuffer();
}

async function createTar(files) {
  const archive = tar.pack();
  const chunks = [];
  const complete = new Promise((resolve, reject) => {
    archive.on("data", (chunk) => chunks.push(chunk));
    archive.on("error", reject);
    archive.on("end", () => resolve(Buffer.concat(chunks)));
  });
  for (const [name, contents] of files) {
    await new Promise((resolve, reject) => {
      archive.entry({ name, size: contents.length, type: "file" }, contents, (error) => error ? reject(error) : resolve());
    });
  }
  archive.finalize();
  return complete;
}

test("converted downloads keep the input filename and exact output extension", () => {
  assert.equal(outputName("My Report.PDF", ".docx"), "My Report.docx");
  assert.equal(outputName("scan.pdf", ".jpg"), "scan.jpg");
  assert.equal(outputName("scan.pdf", ".zip"), "scan.zip");
  assert.equal(outputName("scan.pdf", ".pdf"), "scan.pdf");
  assert.equal(outputName("bundle.tgz", ".tar.gz"), "bundle.tar.gz");
  assert.equal(outputName("../../unsafe name.docx", ".pdf"), "unsafe name.pdf");
});

test("PDF-to-image downloads every page in a correctly named ZIP", async () => {
  await withTempDirectory(async (directory) => {
    const binDirectory = path.join(directory, "bin");
    await mkdir(binDirectory);
    const pdftoppm = path.join(binDirectory, "pdftoppm");
    await writeFile(pdftoppm, "#!/bin/sh\nfor arg do prefix=\"$arg\"; done\nprintf 'page one' > \"$prefix-1.jpg\"\nprintf 'page two' > \"$prefix-2.jpg\"\n");
    await chmod(pdftoppm, 0o755);
    const server = createServer(app);
    const originalPath = process.env.PATH;
    process.env.PATH = `${binDirectory}${path.delimiter}${originalPath || ""}`;

    try {
      await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
      const form = new FormData();
      form.append("file", new Blob(["%PDF-test"], { type: "application/pdf" }), "scan.pdf");
      const response = await fetch(`http://127.0.0.1:${server.address().port}/convert?format=.jpg`, {
        method: "POST",
        body: form,
      });

      assert.equal(response.status, 200);
      assert.match(response.headers.get("content-disposition"), /filename="?scan\.zip"?/);
      const pages = unzipSync(new Uint8Array(await response.arrayBuffer()));
      assert.deepEqual(Object.keys(pages), ["scan-1.jpg", "scan-2.jpg"]);
      assert.equal(new TextDecoder().decode(pages["scan-1.jpg"]), "page one");
      assert.equal(new TextDecoder().decode(pages["scan-2.jpg"]), "page two");
    } finally {
      if (originalPath === undefined) delete process.env.PATH;
      else process.env.PATH = originalPath;
      if (server.listening) {
        await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
      }
    }
  });
});

test("image conversion creates the requested JPEG output", async () => {
  await withTempDirectory(async (directory) => {
    const source = path.join(directory, "source.png");
    const destination = path.join(directory, "converted.jpg");
    await sharp({
      create: { width: 4, height: 4, channels: 4, background: { r: 255, g: 0, b: 0, alpha: 0.5 } },
    }).png().toFile(source);

    await runConversion(source, destination, "source.png", ".jpg", 85);

    assert.equal((await sharp(destination).metadata()).format, "jpeg");
    assert.equal((await sharp(destination).metadata()).channels, 3);
  });
});

test("archive conversion skips unsafe member paths", async () => {
  await withTempDirectory(async (directory) => {
    const source = path.join(directory, "source.tar");
    const destination = path.join(directory, "converted.zip");
    await writeFile(source, await createTar([
      ["folder/document.txt", Buffer.from("hello")],
      ["../outside.txt", Buffer.from("unsafe")],
    ]));

    await runConversion(source, destination, "source.tar", ".zip", 85);

    const contents = unzipSync(await readFile(destination));
    assert.deepEqual(Object.keys(contents), ["folder/document.txt"]);
    assert.equal(new TextDecoder().decode(contents["folder/document.txt"]), "hello");
  });
});

test("invalid PDFs return an explicit conversion error", async () => {
  await withTempDirectory(async (directory) => {
    const binDirectory = path.join(directory, "bin");
    const source = path.join(directory, "broken.pdf");
    const destination = path.join(directory, "optimized.pdf");
    await mkdir(binDirectory);
    await writeFile(source, "not a PDF");
    const ghostscript = path.join(binDirectory, "gs");
    await writeFile(ghostscript, "#!/bin/sh\nexit 1\n");
    await chmod(ghostscript, 0o755);

    const originalPath = process.env.PATH;
    process.env.PATH = `${binDirectory}${path.delimiter}${originalPath || ""}`;
    try {
      await assert.rejects(
        runConversion(source, destination, "broken.pdf", ".pdf", 85),
        (error) => error instanceof ConversionError && error.status === 422 && error.message.includes("Could not optimize"),
      );
    } finally {
      if (originalPath === undefined) delete process.env.PATH;
      else process.env.PATH = originalPath;
    }
  });
});

test("PDF compression uses Ghostscript and keeps the smaller output", async () => {
  await withTempDirectory(async (directory) => {
    const binDirectory = path.join(directory, "bin");
    const source = path.join(directory, "source.pdf");
    const destination = path.join(directory, "compressed.pdf");
    await mkdir(binDirectory);
    await writeFile(source, `%PDF-${"original ".repeat(100)}`);
    const ghostscript = path.join(binDirectory, "gs");
    await writeFile(ghostscript, "#!/bin/sh\nfor arg do case \"$arg\" in -sOutputFile=*) output=\"${arg#*=}\";; esac; done\nprintf '%s' '%PDF-smaller' > \"$output\"\n");
    await chmod(ghostscript, 0o755);

    const originalPath = process.env.PATH;
    process.env.PATH = `${binDirectory}${path.delimiter}${originalPath || ""}`;
    try {
      await runConversion(source, destination, "source.pdf", ".pdf", 85);
    } finally {
      if (originalPath === undefined) delete process.env.PATH;
      else process.env.PATH = originalPath;
    }

    assert.equal(await readFile(destination, "utf8"), "%PDF-smaller");
  });
});

test("PDF compression preserves the input if Ghostscript output is larger", async () => {
  await withTempDirectory(async (directory) => {
    const binDirectory = path.join(directory, "bin");
    const source = path.join(directory, "source.pdf");
    const destination = path.join(directory, "compressed.pdf");
    const original = Buffer.from("%PDF-original");
    await mkdir(binDirectory);
    await writeFile(source, original);
    const ghostscript = path.join(binDirectory, "gs");
    await writeFile(ghostscript, "#!/bin/sh\nfor arg do case \"$arg\" in -sOutputFile=*) output=\"${arg#*=}\";; esac; done\nprintf '%s' '%PDF-output-that-is-larger-than-source' > \"$output\"\n");
    await chmod(ghostscript, 0o755);

    const originalPath = process.env.PATH;
    process.env.PATH = `${binDirectory}${path.delimiter}${originalPath || ""}`;
    try {
      await runConversion(source, destination, "source.pdf", ".pdf", 85);
    } finally {
      if (originalPath === undefined) delete process.env.PATH;
      else process.env.PATH = originalPath;
    }

    assert.deepEqual(await readFile(destination), original);
  });
});

test("text conversion creates a PDF", async () => {
  await withTempDirectory(async (directory) => {
    const source = path.join(directory, "note.txt");
    const destination = path.join(directory, "note.pdf");
    await writeFile(source, "Hello, OptiConverter!");

    await runConversion(source, destination, "note.txt", ".pdf", 85);

    assert.equal((await readFile(destination)).subarray(0, 5).toString(), "%PDF-");
  });
});

test("PDF-to-Word creates an editable DOCX with escaped extracted text", async () => {
  await withTempDirectory(async (directory) => {
    const binDirectory = path.join(directory, "bin");
    const source = path.join(directory, "source.pdf");
    const destination = path.join(directory, "converted.docx");
    await mkdir(binDirectory);
    await writeFile(source, "%PDF-test");
    const commands = {
      pdftotext: "#!/bin/sh\nprintf 'First & <line>\\nSecond line\\n'\n",
      pdfinfo: "#!/bin/sh\nprintf 'Pages:          1\\n'\n",
    };
    for (const [name, contents] of Object.entries(commands)) {
      const commandPath = path.join(binDirectory, name);
      await writeFile(commandPath, contents);
      await chmod(commandPath, 0o755);
    }

    const originalPath = process.env.PATH;
    process.env.PATH = `${binDirectory}${path.delimiter}${originalPath || ""}`;
    try {
      await runConversion(source, destination, "source.pdf", ".docx", 85);
    } finally {
      if (originalPath === undefined) delete process.env.PATH;
      else process.env.PATH = originalPath;
    }

    const archive = unzipSync(await readFile(destination));
    const document = new TextDecoder().decode(archive["word/document.xml"]);
    assert.match(document, /First &amp; &lt;line&gt;/);
    assert.match(document, /Second line/);
    assert.ok(archive["_rels/.rels"]);
    assert.ok(archive["[Content_Types].xml"]);
  });
});

test("PDF-to-Word OCRs every scanned page beyond the previous page limit", async () => {
  await withTempDirectory(async (directory) => {
    const binDirectory = path.join(directory, "bin");
    const source = path.join(directory, "scan.pdf");
    const destination = path.join(directory, "scan.docx");
    await mkdir(binDirectory);
    await writeFile(source, "%PDF-test");
    const commands = {
      pdftotext: "#!/bin/sh\nexit 0\n",
      pdfinfo: "#!/bin/sh\nprintf 'Pages:          21\\n'\n",
      pdftoppm: "#!/bin/sh\npage=1\nwhile [ \"$page\" -le 21 ]; do printf 'page%s' \"$page\" > \"${11}-$page.png\"; page=$((page + 1)); done\n",
    };
    for (const [name, contents] of Object.entries(commands)) {
      const commandPath = path.join(binDirectory, name);
      await writeFile(commandPath, contents);
      await chmod(commandPath, 0o755);
    }

    const originalPath = process.env.PATH;
    process.env.PATH = `${binDirectory}${path.delimiter}${originalPath || ""}`;
    try {
      await runConversion(source, destination, "scan.pdf", ".docx", 85, {
        recognize: async (image) => ({ text: `Scanned page ${image.toString().match(/\d+/)?.[0]}`, confidence: 100 }),
      });
    } finally {
      if (originalPath === undefined) delete process.env.PATH;
      else process.env.PATH = originalPath;
    }

    const archive = unzipSync(await readFile(destination));
    const document = new TextDecoder().decode(archive["word/document.xml"]);
    assert.ok(document.includes("Scanned page 1"));
    assert.ok(document.includes("Scanned page 21"));
    assert.ok(document.indexOf("Scanned page 1") < document.indexOf("Scanned page 21"));
  });
});

test("PDF-to-Word OCRs only the scanned pages of a mixed PDF", async () => {
  await withTempDirectory(async (directory) => {
    const binDirectory = path.join(directory, "bin");
    const source = path.join(directory, "mixed.pdf");
    const destination = path.join(directory, "mixed.docx");
    const calls = path.join(directory, "ocr-calls.txt");
    await mkdir(binDirectory);
    await writeFile(source, "%PDF-test");
    const commands = {
      pdftotext: "#!/bin/sh\nprintf 'Digital page text\\f\\f'\n",
      pdfinfo: "#!/bin/sh\nprintf 'Pages:          2\\n'\n",
      pdftoppm: "#!/bin/sh\nprintf 'page-one' > \"${11}-1.png\"\nprintf 'page-two' > \"${11}-2.png\"\n",
    };
    for (const [name, contents] of Object.entries(commands)) {
      const commandPath = path.join(binDirectory, name);
      await writeFile(commandPath, contents);
      await chmod(commandPath, 0o755);
    }

    const originalPath = process.env.PATH;
    process.env.PATH = `${binDirectory}${path.delimiter}${originalPath || ""}`;
    try {
      await runConversion(source, destination, "mixed.pdf", ".docx", 85, {
        recognize: async (image) => {
          await writeFile(calls, image.toString());
          return { text: "Scanned page text", confidence: 100 };
        },
      });
    } finally {
      if (originalPath === undefined) delete process.env.PATH;
      else process.env.PATH = originalPath;
    }

    const archive = unzipSync(await readFile(destination));
    const document = new TextDecoder().decode(archive["word/document.xml"]);
    const firstPage = document.indexOf("Digital page text");
    const secondPage = document.indexOf("Scanned page text");
    assert.ok(firstPage >= 0 && secondPage > firstPage);
    assert.equal(await readFile(calls, "utf8"), "page-two");
  });
});

test("PDF-to-Word can use a configured OCR endpoint for scanned pages", async () => {
  await withTempDirectory(async (directory) => {
    const binDirectory = path.join(directory, "bin");
    const source = path.join(directory, "scan.pdf");
    const destination = path.join(directory, "scan.docx");
    await mkdir(binDirectory);
    await writeFile(source, "%PDF-test");
    const pdftotext = path.join(binDirectory, "pdftotext");
    await writeFile(pdftotext, "#!/bin/sh\nexit 0\n");
    await chmod(pdftotext, 0o755);

    const server = createServer(async (request, response) => {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      assert.equal(request.method, "POST");
      assert.equal(request.headers["content-type"], "application/pdf");
      assert.equal(request.headers.authorization, "Bearer test-token");
      assert.equal(Buffer.concat(chunks).toString(), "%PDF-test");
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ text: "Text from configured OCR endpoint" }));
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));

    const originalPath = process.env.PATH;
    const originalEndpoint = process.env.OCR_ENDPOINT_URL;
    const originalToken = process.env.OCR_ENDPOINT_TOKEN;
    process.env.PATH = `${binDirectory}${path.delimiter}${originalPath || ""}`;
    process.env.OCR_ENDPOINT_URL = `http://127.0.0.1:${server.address().port}/ocr`;
    process.env.OCR_ENDPOINT_TOKEN = "test-token";
    try {
      await runConversion(source, destination, "scan.pdf", ".docx", 85);
    } finally {
      if (originalPath === undefined) delete process.env.PATH;
      else process.env.PATH = originalPath;
      if (originalEndpoint === undefined) delete process.env.OCR_ENDPOINT_URL;
      else process.env.OCR_ENDPOINT_URL = originalEndpoint;
      if (originalToken === undefined) delete process.env.OCR_ENDPOINT_TOKEN;
      else process.env.OCR_ENDPOINT_TOKEN = originalToken;
      await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    }

    const archive = unzipSync(await readFile(destination));
    const document = new TextDecoder().decode(archive["word/document.xml"]);
    assert.match(document, /Text from configured OCR endpoint/);
  });
});

test("the self-hosted OCR endpoint requires a bearer token and returns recognized text", async () => {
  await withTempDirectory(async (directory) => {
    const binDirectory = path.join(directory, "bin");
    await mkdir(binDirectory);
    const image = await createOcrImage("Self-hosted endpoint OCR text");
    const imageBase64 = image.toString("base64");
    const commands = {
      pdfinfo: "#!/bin/sh\nprintf 'Pages:          1\\n'\n",
      pdftoppm: `#!/bin/sh\nfor arg do prefix="$arg"; done\nprintf '%s' '${imageBase64}' | base64 -d > "$prefix-1.png"\n`,
    };
    for (const [name, contents] of Object.entries(commands)) {
      const commandPath = path.join(binDirectory, name);
      await writeFile(commandPath, contents);
      await chmod(commandPath, 0o755);
    }
    const server = createServer(app);
    const originalPath = process.env.PATH;
    const originalToken = process.env.OCR_ENDPOINT_TOKEN;
    process.env.PATH = `${binDirectory}${path.delimiter}${originalPath || ""}`;
    process.env.OCR_ENDPOINT_TOKEN = "local-test-secret";
    try {
      await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
      const endpoint = `http://127.0.0.1:${server.address().port}/ocr`;
      const unauthorizedResponse = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/pdf" },
        body: "%PDF-test",
      });
      assert.equal(unauthorizedResponse.status, 401);

      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          Authorization: "Bearer local-test-secret",
          "Content-Type": "application/pdf",
        },
        body: "%PDF-test",
      });
      assert.equal(response.status, 200);
      assert.match((await response.json()).text, /Self-hosted endpoint OCR text/i);

      const imageRequest = new FormData();
      imageRequest.append("file", new Blob([image], { type: "image/png" }), "ocr.png");
      const imageResponse = await fetch(`http://127.0.0.1:${server.address().port}/api/ocr`, {
        method: "POST",
        body: imageRequest,
      });
      assert.equal(imageResponse.status, 200);
      const imageResult = await imageResponse.json();
      assert.match(imageResult.text, /Self-hosted endpoint OCR text/i);
      assert.ok(Number.isFinite(imageResult.confidence));
    } finally {
      if (originalPath === undefined) delete process.env.PATH;
      else process.env.PATH = originalPath;
      if (originalToken === undefined) delete process.env.OCR_ENDPOINT_TOKEN;
      else process.env.OCR_ENDPOINT_TOKEN = originalToken;
      if (server.listening) {
        await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
      }
    }
  });
});

test("the API logs request errors but never logs successful conversions", async () => {
  await withTempDirectory(async (directory) => {
    const logPath = path.join(directory, "errors.log");
    const image = await sharp({
      create: { width: 2, height: 2, channels: 3, background: { r: 20, g: 40, b: 60 } },
    }).png().toBuffer();
    const server = createServer(app);
    const originalLogPath = process.env.ERROR_LOG_PATH;
    process.env.ERROR_LOG_PATH = logPath;

    try {
      await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
      const endpoint = `http://127.0.0.1:${server.address().port}/convert`;
      const failedRequest = new FormData();
      failedRequest.append("file", new Blob([image], { type: "image/png" }), "private-image.png");

      const failedResponse = await fetch(`${endpoint}?format=.unsupported`, {
        method: "POST",
        body: failedRequest,
      });
      assert.equal(failedResponse.status, 400);
      const errorLog = await readFile(logPath, "utf8");
      const [record] = errorLog.trim().split("\n").map((line) => JSON.parse(line));
      assert.equal(record.level, "error");
      assert.equal(record.statusCode, 400);
      assert.equal(record.request.path, "/convert");
      assert.doesNotMatch(errorLog, /private-image|success/i);

      const successfulRequest = new FormData();
      successfulRequest.append("file", new Blob([image], { type: "image/png" }), "image.png");
      const successfulResponse = await fetch(`${endpoint}?format=.jpg`, {
        method: "POST",
        body: successfulRequest,
      });
      assert.equal(successfulResponse.status, 200);
      await successfulResponse.arrayBuffer();
      assert.equal(await readFile(logPath, "utf8"), errorLog);
    } finally {
      if (originalLogPath === undefined) delete process.env.ERROR_LOG_PATH;
      else process.env.ERROR_LOG_PATH = originalLogPath;
      if (server.listening) {
        await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
      }
    }
  });
});
