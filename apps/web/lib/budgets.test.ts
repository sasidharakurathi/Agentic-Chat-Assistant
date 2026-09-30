import { describe, expect, it } from "vitest";

import {
  budgetPercent,
  budgetTitle,
  budgetTone,
  limitsOf,
  parseLimit,
  rangeStart,
  resetsLabel,
} from "./budgets";

describe("parseLimit", () => {
  it("reads a limit, no limit, or nonsense", () => {
    expect(parseLimit(" 25 ")).toBe(25);
    expect(parseLimit("$2.504")).toBe(2.5);
    expect(parseLimit("")).toBeNull();
    expect(parseLimit("0")).toBeUndefined();
    expect(parseLimit("0.001")).toBeUndefined();
    expect(parseLimit("ten")).toBeUndefined();
  });
});

describe("the bar", () => {
  it("turns amber from 80% and red at 100%", () => {
    expect(budgetTone("ok")).toBe("bg-success");
    expect(budgetTone("warning")).toBe("bg-warning");
    expect(budgetTone("exceeded")).toBe("bg-destructive");
  });

  it("never draws past full", () => {
    expect(budgetPercent({ ratio: 0.856 })).toBe(85);
    expect(budgetPercent({ ratio: 0.996 })).toBe(99);
    expect(budgetPercent({ ratio: 1.7 })).toBe(100);
  });

  it("says whose budget and when it resets", () => {
    expect(budgetTitle({ scope: "org", period: "day", assistant_name: null })).toBe(
      "Daily: whole organisation",
    );
    expect(budgetTitle({ scope: "assistant", period: "month", assistant_name: "Helper" })).toBe(
      "Monthly: Helper",
    );
    expect(resetsLabel({ period: "day", resets_at: "2026-10-01T00:00:00Z" })).toBe(
      "Resets at midnight UTC",
    );
    expect(resetsLabel({ period: "month", resets_at: "2026-11-01T00:00:00Z" })).toBe(
      "Resets 1 Nov (UTC)",
    );
  });
});

describe("rangeStart", () => {
  const now = new Date("2026-09-29T08:30:00Z");
  it("starts today and this month at UTC midnight", () => {
    expect(rangeStart("today", now)).toBe("2026-09-29T00:00:00.000Z");
    expect(rangeStart("month", now)).toBe("2026-09-01T00:00:00.000Z");
    expect(rangeStart("30d", now)).toBe("2026-08-30T08:30:00.000Z");
  });
});

describe("limitsOf", () => {
  it("fills the form from the scope's own budgets", () => {
    const list = [
      { scope: "org" as const, assistant_id: null, period: "day" as const, limit_usd: 10 },
      { scope: "assistant" as const, assistant_id: "a1", period: "month" as const, limit_usd: 3 },
    ];
    expect(limitsOf(list, null)).toEqual({ daily: "10", monthly: "" });
    expect(limitsOf(list, "a1")).toEqual({ daily: "", monthly: "3" });
  });
});
