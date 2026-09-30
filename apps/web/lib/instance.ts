"use client";

import { useEffect, useState } from "react";

import { meta } from "@/lib/api";

/** Instance-wide settings the builder should see (not per assistant). */

type Flags = { offline: boolean; realModel: boolean };

let flags: Promise<Flags | null> | null = null;

/** Fetched once per page load: these only change when the API restarts. */
function useFlag<T>(pick: (f: Flags) => T, initial: T): T {
  const [value, setValue] = useState(initial);
  useEffect(() => {
    flags ??= meta
      .configSchema()
      .then((r) => ({ offline: r.offline === true, realModel: r.real_model === true }))
      .catch(() => null);
    let live = true;
    void flags.then((f) => {
      if (live && f) setValue(pick(f));
    });
    return () => {
      live = false;
    };
    // `pick` is a fixed accessor per hook below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return value;
}

/** Whether this instance runs in offline mode (`RAG_OFFLINE=1`), which
 *  switches web search off whatever an assistant's config says. */
export function useOfflineMode(): boolean {
  return useFlag((f) => f.offline, false);
}

/** Whether the AI helpers (writing a system prompt, task 5.5) call the real
 *  model and bill for it, or use their free stand-ins. `null` until known. */
export function useRealModel(): boolean | null {
  return useFlag<boolean | null>((f) => f.realModel, null);
}
