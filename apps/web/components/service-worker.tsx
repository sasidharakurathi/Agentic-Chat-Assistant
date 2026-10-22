"use client";

import { useEffect } from "react";

/** Registers public/sw.js (Phase 8.6), whose only job is the offline page.
 *  Production only: in development it would sit between the page and the
 *  dev server's hot reloading. */
export function ServiceWorker() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // No offline page then; the app works the same.
    });
  }, []);
  return null;
}
