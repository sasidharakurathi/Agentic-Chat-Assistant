import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Unit tests for the web app's pure logic (task 1.3): graph editing, layout,
// validation indexing, redirect safety. No DOM. A few render a component to a
// string on the server (react-dom/server), so JSX compiles the way Next
// compiles it (the automatic runtime), not as React.createElement calls.
export default defineConfig({
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: { "@": fileURLToPath(new URL("./", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["**/*.test.ts"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
