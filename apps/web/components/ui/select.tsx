import { ChevronDown } from "lucide-react";
import type { SelectHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

import { fieldClasses } from "./input";

/** A native <select> (so keyboard, screen readers and the mobile picker all
 *  work for free) with the platform arrow replaced: the default one is drawn
 *  by the OS in its own grey and ignores the theme. */
export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className="relative w-full">
      <select
        className={cn(fieldClasses, "h-9 cursor-pointer appearance-none pr-8 pl-3", className)}
        {...props}
      />
      <ChevronDown
        aria-hidden
        className="text-muted-foreground pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2"
      />
    </div>
  );
}
