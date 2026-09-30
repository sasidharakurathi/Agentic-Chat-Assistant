import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

import { Lamp, type LampTone } from "./lamp";

type Variant = "default" | "muted" | "success" | "warning" | "destructive";

const PLATE: Record<Variant, string> = {
  default: "bg-primary text-primary-foreground",
  muted: "bg-muted text-muted-foreground",
  success: "bg-muted text-foreground",
  warning: "bg-muted text-foreground",
  destructive: "bg-muted text-foreground",
};

const TONE: Record<Variant, LampTone | null> = {
  default: null,
  muted: "neutral",
  success: "success",
  warning: "warning",
  destructive: "destructive",
};

/** A status plate (docs/DESIGN.md section 5): radius 4, 22px tall, 12/500,
 *  sentence case. Status variants are a muted plate with a leading lamp in
 *  the status colour and ink text (status text on its own tint fails AA).
 *  `muted` is a plain muted plate; `default` an ink fill. `live` pulses the
 *  lamp (and shows one on `muted`); `lamp={false}` hides it. */
export function Badge({
  className,
  variant = "muted",
  live = false,
  lamp,
  children,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  variant?: Variant;
  /** Pulse the lamp while the state is live (Indexing, Checking). */
  live?: boolean;
  /** Show the lamp. Defaults to on for status variants and live badges. */
  lamp?: boolean;
}) {
  const tone = TONE[variant];
  const showLamp = tone !== null && (lamp ?? (variant !== "muted" || live));
  return (
    <span
      className={cn(
        "text-small inline-flex h-[22px] shrink-0 items-center gap-1.5 rounded-sm px-2 font-medium whitespace-nowrap",
        PLATE[variant],
        className,
      )}
      {...props}
    >
      {showLamp && <Lamp tone={tone} live={live} />}
      {children}
    </span>
  );
}
