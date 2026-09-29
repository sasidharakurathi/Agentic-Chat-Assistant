import { describe, expect, it } from "vitest";

import { effectiveMode, outcome } from "./mcp-rules";

describe("effectiveMode (mirrors approvals.mcp_mode)", () => {
  it("takes the most specific rule set", () => {
    expect(effectiveMode("require", null, null)).toBe("require");
    expect(effectiveMode("require", "auto", null)).toBe("auto");
    expect(effectiveMode("require", "auto", "require")).toBe("require");
    expect(effectiveMode("auto", null, "deny")).toBe("deny");
  });

  it("lets the assistant switch every MCP tool off", () => {
    expect(effectiveMode("deny", "auto", "auto")).toBe("deny");
  });
});

describe("outcome", () => {
  it("runs unasked only for read-only tools under auto", () => {
    expect(outcome("auto", true)).toBe("runs");
    expect(outcome("auto", false)).toBe("asks");
    expect(outcome("auto", null)).toBe("asks");
    expect(outcome("require", true)).toBe("asks");
    expect(outcome("deny", true)).toBe("never");
  });
});
