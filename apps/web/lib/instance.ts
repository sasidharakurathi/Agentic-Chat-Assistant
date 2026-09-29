"use client";

import { useEffect, useState } from "react";

import { meta } from "@/lib/api";

/** Instance-wide settings the builder should see (not per assistant). */

let offline: Promise<boolean> | null = null;

/** Whether this instance runs in offline mode (`RAG_OFFLINE=1`), which
 *  switches web search off whatever an assistant's config says. Fetched
 *  once per page load: it only changes when the API restarts. */
export function useOfflineMode(): boolean {
  const [value, setValue] = useState(false);
  useEffect(() => {
    offline ??= meta
      .configSchema()
      .then((r) => r.offline === true)
      .catch(() => false);
    let live = true;
    void offline.then((v) => {
      if (live) setValue(v);
    });
    return () => {
      live = false;
    };
  }, []);
  return value;
}
