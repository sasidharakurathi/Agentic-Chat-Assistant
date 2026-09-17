import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

type Variant = "default" | "muted" | "success" | "warning" | "destructive";

const styles: Record<Variant, string> = {
  default: "bg-primary text-primary-foreground",
  muted: "bg-muted text-muted-foreground",
  success: "bg-success/15 text-success",
  warning: "bg-warning/20 text-warning",
  destructive: "bg-destructive/15 text-destructive",
};

export function Badge({
  className,
  variant = "muted",
  ...props
}: HTMLAttributes<HTMLSpanElement> & { variant?: Variant }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        styles[variant],
        className,
      )}
      {...props}
    />
  );
}
