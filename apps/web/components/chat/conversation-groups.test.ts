import { describe, expect, it } from "vitest";

import { groupByDay } from "./conversation-groups";

describe("groupByDay", () => {
  const now = new Date(2026, 8, 30, 15, 0);
  const at = (d: number, h = 10) => new Date(2026, 8, d, h).toISOString();

  it("groups by the last message, keeping the list's order", () => {
    const rows = [
      { id: "a", created_at: at(1), last_message_at: at(30, 9) },
      { id: "b", created_at: at(29, 23), last_message_at: null },
      { id: "c", created_at: at(30, 1), last_message_at: at(30, 14) },
      { id: "d", created_at: at(2), last_message_at: at(20) },
    ];
    expect(groupByDay(rows, now).map((g) => [g.label, g.items.map((r) => r.id)])).toEqual([
      ["Today", ["a", "c"]],
      ["Yesterday", ["b"]],
      ["Earlier", ["d"]],
    ]);
  });

  it("leaves out empty groups", () => {
    expect(groupByDay([{ created_at: at(10) }], now).map((g) => g.label)).toEqual(["Earlier"]);
    expect(groupByDay([], now)).toEqual([]);
  });
});
