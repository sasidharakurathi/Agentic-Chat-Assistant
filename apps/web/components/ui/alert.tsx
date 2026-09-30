import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

import { Lamp, type LampTone } from "./lamp";

type Tone = "destructive" | "warning" | "success" | "neutral";

const EDGE: Record<Tone, string> = {
  destructive: "border-destructive",
  warning: "border-warning",
  success: "border-success",
  neutral: "border-border",
};

/** A bordered message in the flow of a form or page: a lamp, an optional
 *  bold first line and the message in ink. Errors say what happened, then
 *  what to do. Destructive messages are `role="alert"` by default, others
 *  `role="status"`; pass `role` to override. */
export function Alert({
  tone = "destructive",
  title,
  children,
  className,
  role,
  ...props
}: HTMLAttributes<HTMLDivElement> & { tone?: Tone; title?: ReactNode }) {
  return (
    <div
      role={role ?? (tone === "destructive" ? "alert" : "status")}
      className={cn(
        "bg-card text-foreground flex items-start gap-2.5 rounded-md border px-3 py-2.5 text-sm",
        EDGE[tone],
        className,
      )}
      {...props}
    >
      <Lamp tone={tone as LampTone} className="mt-1.5" />
      <div className="min-w-0 flex-1 break-words">
        {title && <p className="font-semibold">{title}</p>}
        {children}
      </div>
    </div>
  );
}
