import { describe, expect, it } from "vitest";

import { parsePorts } from "./InternalDestinations";

describe("an internal destination's ports (Phase 9.3)", () => {
  it("reads a list, once each, and drops what isn't a port", () => {
    expect(parsePorts("443, 8443 443")).toEqual([443, 8443]);
    expect(parsePorts("0, 70000, x, 80")).toEqual([80]);
    expect(parsePorts("")).toEqual([]);
  });
});
