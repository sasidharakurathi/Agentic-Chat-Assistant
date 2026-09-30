import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** The top of a page (docs/DESIGN.md section 5): an optional back link
 *  (ChevronLeft plus a text label), the H1 in condensed 26/32 with an
 *  optional status inline, a description (14 muted, max 60ch) and actions,
 *  which sit on the right and wrap below the title under 768px. */
export function PageHeader({
  back,
  title,
  status,
  description,
  actions,
  className,
  titleId,
}: {
  back?: { href: string; label: string };
  title: ReactNode;
  status?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
  /** Id for the H1, e.g. to name a region with `aria-labelledby`. */
  titleId?: string;
}) {
  return (
    <header className={cn("mb-6 flex flex-col gap-2", className)}>
      {back && <BackLink href={back.href} label={back.label} />}
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
            <h1 id={titleId} className="font-condensed text-h1 min-w-0 font-semibold break-words">
              {title}
            </h1>
            {status}
          </div>
          {description && (
            <div className="text-muted-foreground max-w-[60ch] text-sm">{description}</div>
          )}
        </div>
        {actions && (
          <div className="flex shrink-0 flex-wrap items-center gap-2 md:justify-end">{actions}</div>
        )}
      </div>
    </header>
  );
}

/** "‹ Assistants": a back link with a ChevronLeft and a text label. */
export function BackLink({
  href,
  label,
  className,
}: {
  href: string;
  label: string;
  className?: string;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "text-label text-muted-foreground hover:text-foreground focus-visible:ring-ring -ml-1 inline-flex w-fit items-center gap-1 rounded-sm px-1 py-0.5 font-medium transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none",
        className,
      )}
    >
      <ChevronLeft aria-hidden className="size-4" />
      {label}
    </Link>
  );
}
