import { writeFileSync } from "node:fs";

import { STATE_FILE, seed } from "./seed";

/** Seed the throwaway stack once, before the tests (Phase 8.11). */
export default async function globalSetup(): Promise<void> {
  writeFileSync(STATE_FILE, JSON.stringify(await seed(), null, 2), "utf-8");
}
