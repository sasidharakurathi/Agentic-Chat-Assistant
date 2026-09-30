"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

/** A quiet loading line (docs/DESIGN.md section 5): "Loading assistants…"
 *  in 12px muted text, shown only after 300ms so fast loads never flash.
 *  The status region is there from the start so the line is announced.
 *  Optional skeleton rows at the real row height; they pulse only when
 *  motion is allowed. */
export function Loading({
  what,
  rows = 0,
  rowHeight = 48,
  delay = 300,
  className,
}: {
  /** What is loading, in words: "assistants", "your workspace". */
  what: string;
  /** Skeleton rows to draw below the line. */
  rows?: number;
  /** Height of one skeleton row in px, to match the real rows. */
  rowHeight?: number;
  /** Milliseconds before anything shows. */
  delay?: number;
  className?: string;
}) {
  const [visible, setVisible] = useState(delay <= 0);
  useEffect(() => {
    if (delay <= 0) return;
    const t = setTimeout(() => setVisible(true), delay);
    return () => clearTimeout(t);
  }, [delay]);

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <p role="status" className="text-small text-muted-foreground min-h-[17px]">
        {visible ? `Loading ${what}…` : ""}
      </p>
      {visible && rows > 0 && (
        <div aria-hidden className="border-border flex flex-col border-t">
          {Array.from({ length: rows }, (_, i) => (
            <div
              key={i}
              className="border-border flex items-center border-b"
              style={{ height: rowHeight }}
            >
              <div className="bg-muted h-3 w-1/3 rounded-sm motion-safe:animate-pulse" />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
