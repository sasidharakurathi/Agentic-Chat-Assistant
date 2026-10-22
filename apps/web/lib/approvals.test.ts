import { describe, expect, it } from "vitest";

import {
  approvalQuestion,
  closesIn,
  editedParams,
  inputAddsDetail,
  isOpen,
  pollDelayMs,
  requestLine,
  riskLabel,
  titleWithPending,
} from "./approvals";

describe("requests that wait (Phase 9.1)", () => {
  it("knows which are still to happen", () => {
    expect(["pending", "approved", "executing"].every(isOpen)).toBe(true);
    expect(["succeeded", "failed", "denied", "expired", "cancelled"].some(isOpen)).toBe(false);
  });

  it("looks again soon while one runs, now and then while one waits, never after", () => {
    expect(pollDelayMs(["pending", "executing"])).toBe(2500);
    expect(pollDelayMs(["pending"])).toBe(20_000);
    expect(pollDelayMs(["succeeded"])).toBeNull();
    expect(pollDelayMs([])).toBeNull();
  });

  it("says who it waits for", () => {
    expect(requestLine("pending", "asker", true)).toBe("Waiting for you to confirm");
    expect(requestLine("pending", "asker", false)).toBe("Waiting for them to confirm");
    expect(requestLine("pending", "approver", true)).toBe("Waiting for an approver");
    expect(requestLine("failed", "approver", true)).toBe("Didn't go through");
  });

  it("says when it closes in the largest unit that fits", () => {
    const now = Date.UTC(2026, 0, 1);
    const at = (ms: number) => new Date(now + ms).toISOString();
    expect(closesIn(at(40 * 60_000), now)).toBe("closes in 40 minutes");
    expect(closesIn(at(5 * 3_600_000), now)).toBe("closes in 5 hours");
    expect(closesIn(at(72 * 3_600_000), now)).toBe("closes in 3 days");
    expect(closesIn(at(-1), now)).toBe("closed");
    expect(closesIn(null, now)).toBeNull();
  });

  it("sends back what was typed, leaving blanks out and booleans as booleans", () => {
    const fields = [
      { name: "app", type: "string" },
      { name: "reason", type: "string" },
      { name: "urgent", type: "boolean" },
    ];
    expect(editedParams(fields, { app: "Figma", reason: "  ", urgent: true })).toEqual({
      app: "Figma",
      urgent: true,
    });
  });
});

describe("titleWithPending", () => {
  it("puts the count in front once, and takes it away", () => {
    const one = titleWithPending("Assistant Studio", 1);
    expect(one).toBe("(1) Approval needed · Assistant Studio");
    expect(titleWithPending(one, 2)).toBe("(2) Approval needed · Assistant Studio");
    expect(titleWithPending(one, 0)).toBe("Assistant Studio");
  });
});

describe("inputAddsDetail", () => {
  it("hides the input when the statement already is it", () => {
    const sql = "DELETE FROM sessions WHERE expired";
    expect(inputAddsDetail(sql, { connection_id: "c1", sql })).toBe(true);
    expect(inputAddsDetail(sql, { sql })).toBe(false);
    expect(inputAddsDetail("create_ticket", { title: "Broken lamp", priority: 2 })).toBe(true);
    expect(inputAddsDetail("x", {})).toBe(false);
  });
});

describe("approvalQuestion", () => {
  it("asks about the change in plain words", () => {
    expect(approvalQuestion("mcp__caps__sql_query")).toBe("Approve this database change?");
    expect(approvalQuestion("mcp__caps__http_request")).toBe("Approve this web request?");
    expect(approvalQuestion("mcp__tickets__create_ticket")).toBe(
      "Approve create_ticket on tickets?",
    );
    expect(approvalQuestion("Bash")).toBe("Approve Bash?");
  });
});

describe("riskLabel", () => {
  it("reads in sentence case", () => {
    expect(riskLabel("high")).toBe("High risk");
    expect(riskLabel("low")).toBe("Low risk");
  });
});
