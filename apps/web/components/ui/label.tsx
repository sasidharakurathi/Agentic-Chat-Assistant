import type { LabelHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** Form label, 13/18 medium. Hints go below the field in 12px muted text,
 *  linked with `aria-describedby`. */
export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("text-foreground text-label font-medium", className)} {...props} />;
}
