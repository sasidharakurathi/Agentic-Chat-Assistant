// The browser tests' own API (Phase 8.11): a fresh SQLite database in a
// temporary folder, the fake agent driver (APP_ENV=test forces it, so a
// test can never spend), mail kept in memory, answers in memory instead of
// Redis. It never touches the dev database or the dev servers' ports.
//
// Playwright starts this (playwright.config.ts) and stops it after the run.

import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const apiDir = path.resolve(here, "../../api");
const python =
  process.env.E2E_PYTHON ??
  path.resolve(
    here,
    process.platform === "win32"
      ? "../../../.venv/Scripts/python.exe"
      : "../../../.venv/bin/python",
  );

export const API_PORT = 8020;
const dir = mkdtempSync(path.join(tmpdir(), "as-e2e-"));
const db = path.join(dir, "e2e.db").replaceAll("\\", "/");

const env = {
  ...process.env,
  APP_ENV: "test",
  DATABASE_URL: `sqlite+aiosqlite:///${db}`,
  JWT_SECRET: "e2e-only-secret-value-that-is-long-enough-xxxxxxxxx",
  AGENT_DRIVER: "fake",
  // Each answer takes a few seconds, so a test can drop the connection
  // halfway through one.
  AGENT_FAKE_DELAY_MS: "4000",
  RAG_OFFLINE: "1",
  MAIL_BACKEND: "memory",
  REGISTRATION: "open",
  ORG_MODE: "multi",
  RATE_LIMIT_ENABLED: "0",
  PRIVACY_NOTICE_REQUIRED: "1",
  CORS_ORIGINS: "http://localhost:3020,http://127.0.0.1:3020",
  AGENT_STATE_DIR: path.join(dir, "agent"),
  MCP_RUNNER_URL: "",
  LANGFUSE_HOST: "",
  OTEL_EXPORTER_OTLP_ENDPOINT: "",
  // Whatever the repo's .env holds: nothing leaves this machine.
  ANTHROPIC_API_KEY: "",
  VOYAGE_API_KEY: "",
  SMTP_HOST: "",
  LOG_FORMAT: "console",
  // Redis for the conversation's session key: the dev one, on its own
  // database number so nothing mixes with dev data (CI runs its own).
  // Without one, every turn waits out a connection timeout.
  REDIS_URL: process.env.E2E_REDIS_URL ?? "redis://127.0.0.1:46379/15",
};

// The schema from the models, as the API's own tests make it: the
// migrations write Postgres's `now()` as column defaults, which SQLite
// can't run (the Postgres tier checks the migrations).
const SCHEMA = [
  "import asyncio",
  "import app.models",
  "from app.db.base import Base",
  "from app.db.session import get_engine",
  "async def main():",
  "    async with get_engine().begin() as conn:",
  "        await conn.run_sync(Base.metadata.create_all)",
  "asyncio.run(main())",
].join("\n");
const made = spawnSync(python, ["-c", SCHEMA], { cwd: apiDir, env, stdio: "inherit" });
if (made.status !== 0) {
  console.error("[e2e] making the database failed");
  process.exit(1);
}

const server = spawn(
  python,
  ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(API_PORT)],
  { cwd: apiDir, env, stdio: "inherit" },
);

const stop = () => {
  server.kill();
  rmSync(dir, { recursive: true, force: true });
};
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
process.on("exit", stop);
server.on("exit", (code) => process.exit(code ?? 0));
