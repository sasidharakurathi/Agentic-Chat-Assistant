import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** What a lamp says. Red, ochre and green are the only lamp colours, and a
 *  line is never one of them. `off` is a hollow ring ("not checked"),
 *  `neutral` a muted dot for states that are neither good nor bad. */
export type LampTone = "success" | "warning" | "destructive" | "neutral" | "off";

const FILL: Record<LampTone, string> = {
  success: "bg-success text-success-foreground",
  warning: "bg-warning text-warning-foreground",
  destructive: "bg-destructive text-destructive-foreground",
  neutral: "bg-muted-foreground text-background",
  off: "border-muted-foreground border-[1.5px] bg-transparent text-foreground",
};

/** A status lamp (docs/DESIGN.md section 5): an 8px circle, or 20px holding
 *  a count. Always next to a text label, or given one with `label` (which
 *  makes it an image with that accessible name; without it the lamp is
 *  hidden from screen readers as decoration). `live` pulses it while the
 *  state is live; under reduced motion it stays steady. */
export function Lamp({
  tone = "neutral",
  count,
  live = false,
  label,
  className,
  ...props
}: Omit<HTMLAttributes<HTMLSpanElement>, "children"> & {
  tone?: LampTone;
  count?: number;
  live?: boolean;
  label?: string;
}) {
  const withCount = count !== undefined;
  return (
    <span
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full",
        withCount ? "num text-small h-5 min-w-5 px-1 font-semibold" : "size-2",
        FILL[tone],
        live && "lamp-live",
        className,
      )}
      {...props}
    >
      {withCount ? count : null}
    </span>
  );
}
