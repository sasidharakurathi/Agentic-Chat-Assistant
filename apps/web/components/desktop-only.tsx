import { Monitor } from "lucide-react";

import { cn } from "@/lib/utils";

/** Said wherever signing in would be offered on a phone: signing in and
 *  creating an account are for a computer (the builder is a canvas). The
 *  landing page and the auth pages show it below `md` in place of their
 *  forms and buttons. */
export function DesktopOnlyNote({ className }: { className?: string }) {
  return (
    <div
      role="note"
      className={cn("bg-card border-border flex gap-3 rounded-lg border px-4 py-3.5", className)}
    >
      <Monitor aria-hidden className="text-muted-foreground mt-0.5 size-5 shrink-0" />
      <div className="text-sm">
        <p className="font-semibold">Sign in from a computer</p>
        <p className="text-muted-foreground mt-1">
          Assistant Studio is built on a canvas that needs a larger screen, so signing in and
          creating an account work on desktop only.
        </p>
      </div>
    </div>
  );
}
