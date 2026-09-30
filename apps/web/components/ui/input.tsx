import type { InputHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** Shared look for text fields (Input, Select, Textarea): radius 6, `field`
 *  fill, 1px `field-border` that firms up on hover, and on focus a crisp 2px
 *  ink edge (border plus a 1px ring), no soft halo. `aria-invalid` turns the
 *  edge destructive. */
export const fieldClasses =
  "bg-field border-field-border hover:border-field-border-hover text-foreground placeholder:text-muted-foreground w-full rounded-md border text-sm transition-[border-color,box-shadow] duration-120 ease-out outline-none focus-visible:border-ring focus-visible:ring-1 focus-visible:ring-ring aria-invalid:border-destructive aria-invalid:focus-visible:ring-destructive disabled:cursor-not-allowed disabled:opacity-50";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(fieldClasses, "flex h-9 px-3 py-1", className)} {...props} />;
}
