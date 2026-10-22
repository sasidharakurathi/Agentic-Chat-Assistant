"use client";

import { useEffect, useState } from "react";

import { approvals, type ApprovalSummary } from "@/lib/api";

/** How often the counts are read again while a page is open. */
export const SUMMARY_EVERY_MS = 60_000;

/** The counts the navigation shows (Phase 9.2): requests waiting for me to
 *  decide, and mine still open. Null until the first answer, or when the
 *  server can't be reached (the counts are a convenience). */
export function useApprovalSummary(enabled: boolean): ApprovalSummary | null {
  const [summary, setSummary] = useState<ApprovalSummary | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let gone = false;
    const read = () =>
      approvals
        .summary()
        .then((s) => !gone && setSummary(s))
        .catch(() => !gone && setSummary(null));
    void read();
    const timer = setInterval(() => void read(), SUMMARY_EVERY_MS);
    return () => {
      gone = true;
      clearInterval(timer);
    };
  }, [enabled]);
  return summary;
}

/** "3 waiting", or nothing for none. */
export function waitingWords(count: number | null | undefined): string | null {
  if (!count) return null;
  return `${count} waiting`;
}
