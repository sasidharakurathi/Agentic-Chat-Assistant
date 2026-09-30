import type { TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

import { fieldClasses } from "./input";

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(fieldClasses, "flex min-h-20 px-3 py-2", className)} {...props} />;
}
