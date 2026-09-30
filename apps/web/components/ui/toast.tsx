"use client";

import { X } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { Lamp, type LampTone } from "./lamp";

type Tone = "default" | "success" | "error";
type Toast = { id: number; message: string; tone: Tone };

const Ctx = createContext<((message: string, tone?: Tone) => void) | null>(null);

/** How long a non-error toast stays up. Errors stay until dismissed. */
const LIFETIME_MS = 5000;

const LAMP: Record<Tone, LampTone> = {
  default: "neutral",
  success: "success",
  error: "destructive",
};

/** Brief confirmations ("Published version 3") that should not take over
 *  the page (task 0.4). Announced through a polite live region, so a screen
 *  reader hears them too; errors use an assertive one and stay until
 *  closed, so there is time to read what to do. Others leave after 5s,
 *  pausing while hovered or focused. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const show = useCallback((message: string, tone: Tone = "default") => {
    const id = next.current++;
    setToasts((t) => [...t, { id, message, tone }]);
  }, []);

  const region = (tone: "polite" | "assertive", items: Toast[]) => (
    <div aria-live={tone} role={tone === "assertive" ? "alert" : "status"} className="contents">
      {items.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
      ))}
    </div>
  );

  return (
    <Ctx.Provider value={show}>
      {children}
      <div className="pointer-events-none fixed inset-x-4 bottom-4 z-50 flex flex-col items-stretch gap-2 sm:left-auto sm:items-end">
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

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  const [paused, setPaused] = useState(false);
  const remaining = useRef(LIFETIME_MS);
  const persistent = toast.tone === "error";
  // The provider re-creates onDismiss on every render; the timer must not
  // restart because of that.
  const dismissRef = useRef(onDismiss);
  useEffect(() => {
    dismissRef.current = onDismiss;
  }, [onDismiss]);

  useEffect(() => {
    if (persistent || paused) return;
    const started = Date.now();
    const timer = setTimeout(() => dismissRef.current(), remaining.current);
    return () => {
      clearTimeout(timer);
      remaining.current = Math.max(0, remaining.current - (Date.now() - started));
    };
  }, [persistent, paused]);

  return (
    <div
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      className="bg-card border-border text-foreground shadow-float animate-float-in pointer-events-auto flex w-full items-start gap-3 rounded-lg border py-2.5 pr-2 pl-3.5 text-sm sm:w-auto sm:max-w-sm"
    >
      <Lamp tone={LAMP[toast.tone]} className="mt-1.5" />
      <p className="min-w-0 flex-1 py-0.5 break-words">{toast.message}</p>
      <button
        type="button"
        aria-label="Dismiss"
        onClick={onDismiss}
        className="text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-ring -my-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-md transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none"
      >
        <X aria-hidden className="size-4" />
      </button>
    </div>
  );
}

export function useToast() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast needs a <ToastProvider>");
  return ctx;
}
