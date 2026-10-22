import { describe, expect, it } from "vitest";

import { actionName, TIER_WORDS } from "./actions";

describe("declared actions in the builder (Phase 9.1)", () => {
  it("names an action from its title the way the server accepts", () => {
    expect(actionName("Request access to an app")).toBe("request_access_to_an_app");
    expect(actionName("  Close my ticket!  ")).toBe("close_my_ticket");
    expect(actionName("2FA reset")).toBe("a_2fa_reset");
    expect(actionName("")).toBe("");
    const long = actionName("x".repeat(60));
    expect(long.length).toBeLessThanOrEqual(40);
    expect(/^[a-z][a-z0-9_]*$/.test(long)).toBe(true);
  });

  it("says how each tier runs", () => {
    expect(TIER_WORDS.confirm).toBe("Asker confirms");
    expect(Object.keys(TIER_WORDS)).toEqual(["read", "confirm", "approver"]);
  });
});
