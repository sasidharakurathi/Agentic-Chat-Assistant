import { describe, expect, it } from "vitest";

import { employeeMayOpen, isEmployee } from "./roles";

describe("what an employee reaches (Phase 8.1)", () => {
  it("is Chat, a live conversation and their account", () => {
    expect(employeeMayOpen("/chat")).toBe(true);
    expect(employeeMayOpen("/account")).toBe(true);
    expect(employeeMayOpen("/shared/abc")).toBe(true);
    expect(employeeMayOpen("/assistants/abc/chat")).toBe(true);
    expect(employeeMayOpen("/assistants/abc/chat", "?conversation=x")).toBe(true);
    // The stable link to a conversation, from an email (Phase 8.6).
    expect(employeeMayOpen("/chat/c/abc")).toBe(true);
    // Their queue, when an assistant names them its approver (Phase 9.1).
    expect(employeeMayOpen("/approvals")).toBe(true);
    // A request's own page, where emails link (Phase 9.2).
    expect(employeeMayOpen("/approvals/abc")).toBe(true);
  });

  it("is not the Studio, nor a preview", () => {
    expect(employeeMayOpen("/assistants")).toBe(false);
    expect(employeeMayOpen("/assistants/abc/build")).toBe(false);
    expect(employeeMayOpen("/assistants/abc/chat", "?preview=1")).toBe(false);
    expect(employeeMayOpen("/members")).toBe(false);
    expect(employeeMayOpen("/audit")).toBe(false);
  });

  it("knows the role", () => {
    expect(isEmployee("employee")).toBe(true);
    expect(isEmployee("member")).toBe(false);
    expect(isEmployee(null)).toBe(false);
  });
});
