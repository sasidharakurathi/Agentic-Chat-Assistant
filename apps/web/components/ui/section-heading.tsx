import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

const SIZE = {
  2: "text-h2 font-semibold",
  3: "text-h3 font-semibold",
  4: "text-h4 font-semibold",
} as const;

/** A section heading (docs/DESIGN.md section 5): H2 20/26, H3 16/22 or H4
 *  14/20, all sans 600 in sentence case, with an optional one-line
 *  description and actions on the right. Replaces every uppercase,
 *  letter-spaced label. `id` and `tabIndex` go on the heading itself, so a
 *  table of contents can scroll to it and move focus there. */
export function SectionHeading({
  level = 2,
  title,
  description,
  actions,
  className,
  id,
  tabIndex,
}: {
  level?: 2 | 3 | 4;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
  id?: string;
  tabIndex?: number;
}) {
  const Tag = `h${level}` as const;
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-x-4 gap-y-2", className)}>
      <div className="flex min-w-0 flex-col gap-0.5">
        <Tag
          id={id}
          tabIndex={tabIndex}
          className={cn(SIZE[level], "scroll-mt-6 focus-visible:rounded-sm")}
        >
          {title}
        </Tag>
        {description && (
          <div className="text-muted-foreground max-w-[60ch] text-sm">{description}</div>
        )}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
