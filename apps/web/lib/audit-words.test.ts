import { describe, expect, it } from "vitest";

import { AUDIT_KINDS, auditDetail, describeAction } from "./audit-words";

describe("the audit log in words (Phase 7b.7)", () => {
  it("says what happened, and keeps the name of anything unlisted", () => {
    expect(describeAction("conversation.read_by_admin")).toBe("Opened someone's conversation");
    expect(describeAction("something.new")).toBe("something.new");
  });

  it("shows the one detail worth showing", () => {
    expect(auditDetail("conversation.read_by_admin", { reason: "Ticket 4" })).toBe(
      "Reason: Ticket 4",
    );
    expect(auditDetail("assistant.version.approve", { version: 2, self_approved: true })).toBe(
      "Self-approved: nobody else could review",
    );
    expect(auditDetail("assistant.version.submit", { version: 3 })).toBe("Version 3");
    expect(auditDetail("user.login", {})).toBeNull();
  });

  it("offers the admins' conversation reads as their own view", () => {
    expect(AUDIT_KINDS.map((k) => k.value)).toContain("conversation.read_by_admin");
  });
});
