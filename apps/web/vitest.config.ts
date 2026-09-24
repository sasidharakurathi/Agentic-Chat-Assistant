import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Unit tests for the web app's pure logic (task 1.3): graph editing, layout,
// validation indexing, redirect safety. No DOM; nothing here renders.
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["**/*.test.ts"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
