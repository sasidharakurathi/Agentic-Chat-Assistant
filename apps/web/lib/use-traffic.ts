"use client";

import { useEffect, useState } from "react";

import { ApiError, analytics, type AssistantTraffic } from "@/lib/api";
import { failureMessage } from "@/lib/format";

/** How often the canvas asks again while Traffic is on. */
export const TRAFFIC_POLL_MS = 15_000;

/** Traffic on the canvas (Phase 10.2): the assistant's live calls, errors
 *  and approvals per station in the last `hours`, asked for again every 15
 *  seconds while `on`. A hidden tab skips its turn rather than polling a
 *  page nobody is looking at. Off, it holds nothing. */
export function useTraffic(assistantId: string, on: boolean, hours = 24) {
  const [traffic, setTraffic] = useState<AssistantTraffic | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTraffic(null);
    setError(null);
    if (!on) return;
    let live = true;
    let timer = 0;
    const schedule = () => {
      if (live) timer = window.setTimeout(() => void load(), TRAFFIC_POLL_MS);
    };
    const load = async () => {
      if (document.visibilityState === "hidden") {
        schedule();
        return;
      }
      try {
        const t = await analytics.traffic(assistantId, hours);
        if (!live) return;
        setTraffic(t);
        setError(null);
      } catch (err) {
        if (!live) return;
        setError(
          failureMessage(
            "Couldn't load the traffic.",
            err instanceof ApiError ? err.message : null,
            "It will try again in a few seconds.",
          ),
        );
      } finally {
        schedule();
      }
    };
    void load();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [assistantId, on, hours]);

  return { traffic, error };
}
