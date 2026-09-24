import { describe, expect, it } from "vitest";

import { formatCount, formatUsd } from "./format";

describe("formatUsd", () => {
  it.each([
    [0, "$0.00"],
    ["0.000000", "$0.00"],
    [0.00004, "<$0.0001"],
    [0.0042, "$0.0042"],
    ["0.004200", "$0.0042"],
    [0.5, "$0.5000"],
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
