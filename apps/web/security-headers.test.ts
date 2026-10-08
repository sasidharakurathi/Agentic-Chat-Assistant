/** The headers every page carries (task 6.3, Phase 7a.2), read from the real
 *  Next config so a loosened policy fails here, not in a security review. */
import { describe, expect, it } from "vitest";

import nextConfig from "./next.config";

async function csp(): Promise<string> {
  const rules = (await nextConfig.headers?.()) ?? [];
  const all = rules.flatMap((r) => r.headers);
  return all.find((h) => h.key === "Content-Security-Policy")?.value ?? "";
}

describe("the page's content policy", () => {
  it("loads images only from this site (model output cannot fetch another address)", async () => {
    const policy = await csp();
    const img = policy
      .split(";")
      .map((d) => d.trim())
      .find((d) => d.startsWith("img-src"));
    expect(img).toBe("img-src 'self' data: blob:");
  });

  it("keeps the framing, plugin and base rules", async () => {
    const policy = await csp();
    expect(policy).toContain("frame-ancestors 'none'");
    expect(policy).toContain("object-src 'none'");
    expect(policy).toContain("base-uri 'self'");
  });
});
