"use client";

import { useEffect, useState } from "react";

/** The approval still's clock: counts down from 4:32 a second at a time,
 *  as the real card does, and starts over at 4:59 rather than reach 0:00. */
export function Countdown({ from = 272 }: { from?: number }) {
  const [left, setLeft] = useState(from);
  useEffect(() => {
    const id = window.setInterval(() => setLeft((s) => (s <= 1 ? 299 : s - 1)), 1000);
    return () => window.clearInterval(id);
  }, []);
  const m = Math.floor(left / 60);
  const s = String(left % 60).padStart(2, "0");
  return (
    <span className="text-small text-muted-foreground num">
      {m}:{s} left
    </span>
  );
}
