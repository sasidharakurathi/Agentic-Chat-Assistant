import { describe, expect, it } from "vitest";

import { formatCount, formatDateTime, formatUsd } from "./format";

describe("formatUsd", () => {
  it.each([
    [0, "$0.00"],
    ["0.000000", "$0.00"],
    [0.00004, "<$0.0001"],
    [0.0042, "$0.0042"],
    ["0.004200", "$0.0042"],
    [0.5, "$0.50"],
    [0.045, "$0.045"],
    [0.12, "$0.12"],
    [0.0369, "$0.0369"],
    [1.234, "$1.23"],
    [-1, "$0.00"],
    ["not a number", "$0.00"],
  ])("%j -> %s", (amount, expected) => {
    expect(formatUsd(amount)).toBe(expected);
  });
});

describe("formatCount", () => {
  it.each([
    [0, "0"],
    [950, "950"],
    [1234, "1.2k"],
    [12_345, "12k"],
    [2_500_000, "2.5M"],
  ])("%d -> %s", (n, expected) => {
    expect(formatCount(n)).toBe(expected);
  });
});

describe("formatDateTime", () => {
  it("adds the time to the date, and the year only when it differs", () => {
    const now = new Date(2026, 9, 12, 9, 0);
    expect(formatDateTime(new Date(2026, 2, 12, 14, 5).toISOString(), now)).toBe("12 Mar, 14:05");
    expect(formatDateTime(new Date(2025, 2, 12, 9, 30).toISOString(), now)).toBe(
      "12 Mar 2025, 09:30",
    );
    expect(formatDateTime("not a date", now)).toBe("");
  });
});
