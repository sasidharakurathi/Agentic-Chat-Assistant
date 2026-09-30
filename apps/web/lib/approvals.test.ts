import { describe, expect, it } from "vitest";

import { formatCountdown, inputAddsDetail, titleWithPending } from "./approvals";

describe("formatCountdown", () => {
  it("reads as minutes and seconds", () => {
    expect(formatCountdown(298)).toBe("4:58");
    expect(formatCountdown(9)).toBe("0:09");
    expect(formatCountdown(-3)).toBe("0:00");
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
