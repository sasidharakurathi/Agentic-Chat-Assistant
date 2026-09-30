import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Nothing here yet (docs/DESIGN.md section 5). A plate with a dashed
 *  border, the same meaning as a dashed line on the canvas: not in service
 *  yet. An H3, one sentence on what goes here and why, and the action. */
export function EmptyState({
  title,
  description,
  action,
  icon,
  className,
  headingLevel = 3,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  /** An optional glyph or bullet shown before the title. */
  icon?: ReactNode;
  className?: string;
  headingLevel?: 2 | 3 | 4;
}) {
  const Tag = `h${headingLevel}` as const;
  return (
    <div
      className={cn(
        "border-field-border bg-card flex flex-col items-start gap-3 rounded-lg border border-dashed p-6",
        className,
      )}
    >
      <div className="flex items-center gap-2">
        {icon}
        <Tag className="text-h3 font-semibold">{title}</Tag>
      </div>
      {description && (
        <div className="text-muted-foreground max-w-[60ch] text-sm">{description}</div>
      )}
      {action && <div className="mt-1 flex flex-wrap items-center gap-2">{action}</div>}
    </div>
  );
}
