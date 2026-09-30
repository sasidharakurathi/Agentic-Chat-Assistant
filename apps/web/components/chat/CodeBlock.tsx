"use client";

import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Code, exactly as it ran or will run: SQL, a tool's input and output, a
 *  fenced block in an answer. Fira Code on the muted code ground, wrapped
 *  rather than scrolled sideways, with a Copy button. `size="sm"` is the
 *  run trace's 12/18. `children` overrides what is drawn (a Markdown code
 *  element) while `code` stays what is copied. */
export function CodeBlock({
  code,
  children,
  size = "md",
  maxHeight = true,
  label,
  className,
}: {
  code: string;
  children?: ReactNode;
  size?: "sm" | "md";
  /** Cap the height and scroll inside, for long output. */
  maxHeight?: boolean;
  /** Accessible name of the block, e.g. "SQL". */
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState<"yes" | "failed" | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied("yes");
    } catch {
      setCopied("failed");
    }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(null), 2000);
  };

  return (
    <div className={cn("group/code relative min-w-0", className)}>
      <pre
        aria-label={label}
        tabIndex={maxHeight ? 0 : undefined}
        className={cn(
          "bg-muted border-border text-foreground focus-visible:ring-ring overflow-auto rounded-md border py-2.5 pr-20 pl-3 font-mono break-words whitespace-pre-wrap focus-visible:ring-2 focus-visible:outline-none",
          size === "sm" ? "text-small" : "text-code",
          maxHeight && "max-h-80",
        )}
      >
        {children ?? code}
      </pre>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => void copy()}
        className="absolute top-1.5 right-1.5 h-7 px-2"
        aria-label={label ? `Copy ${label}` : "Copy code"}
      >
        {copied === "yes" ? <Check aria-hidden /> : <Copy aria-hidden />}
        <span aria-hidden>{copied === "yes" ? "Copied" : "Copy"}</span>
      </Button>
      <span role="status" className="sr-only">
        {copied === "yes"
          ? "Copied"
          : copied === "failed"
            ? "Couldn't copy. Select the text and copy it by hand."
            : ""}
      </span>
    </div>
  );
}
