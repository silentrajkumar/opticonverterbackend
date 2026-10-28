import { appendFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export async function logError(error, { request, statusCode } = {}) {
  const record = {
    timestamp: new Date().toISOString(),
    level: "error",
    statusCode,
    error: {
      name: error instanceof Error ? error.name : "Error",
      message: error instanceof Error ? error.message : String(error),
      ...(error?.code ? { code: error.code } : {}),
    },
    ...(request ? { request: { method: request.method, path: request.path } } : {}),
  };
  const logPath = path.resolve(process.env.ERROR_LOG_PATH || path.join(projectRoot, "logs", "errors.log"));

  try {
    await mkdir(path.dirname(logPath), { recursive: true });
    await appendFile(logPath, `${JSON.stringify(record)}\n`, "utf8");
  } catch (loggingError) {
    console.error("Could not write error log", loggingError);
  }
}
