"use client";

import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

type Tone = "default" | "success" | "error";
type Toast = { id: number; message: string; tone: Tone };

const Ctx = createContext<((message: string, tone?: Tone) => void) | null>(null);

const LIFETIME_MS = 4000;

/** Brief confirmations ("Published v3") that should not take over the page
 *  (task 0.4). Announced through a polite live region, so a screen reader
 *  hears them too; errors use an assertive one. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(0);

  const show = useCallback((message: string, tone: Tone = "default") => {
    const id = next.current++;
    setToasts((t) => [...t, { id, message, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), LIFETIME_MS);
  }, []);

  const region = (tone: "polite" | "assertive", items: Toast[]) => (
    <div aria-live={tone} role={tone === "assertive" ? "alert" : "status"} className="contents">
      {items.map((t) => (
        <div
          key={t.id}
          className={cn(
            "bg-background border-border rounded-md border px-4 py-2 text-sm shadow-lg",
            t.tone === "success" && "border-emerald-600/40",
            t.tone === "error" && "border-destructive/60 text-destructive",
          )}
        >
          {t.message}
        </div>
      ))}
    </div>
  );

  return (
    <Ctx.Provider value={show}>
      {children}
      <div className="pointer-events-none fixed right-4 bottom-4 z-50 flex flex-col items-end gap-2">
        {region(
          "polite",
          toasts.filter((t) => t.tone !== "error"),
        )}
        {region(
          "assertive",
          toasts.filter((t) => t.tone === "error"),
        )}
      </div>
    </Ctx.Provider>
  );
}

export function useToast() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast needs a <ToastProvider>");
  return ctx;
}
