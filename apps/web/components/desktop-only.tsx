import { Monitor } from "lucide-react";

import { cn } from "@/lib/utils";

/** Said on a phone where building is offered (Phase 8.6): the builder is a
 *  canvas that needs a computer; Chat, and signing in to it, work here.
 *  The landing page shows it below `md`, beside its buttons. */
export function DesktopOnlyNote({ className }: { className?: string }) {
  return (
    <div
      role="note"
      className={cn("bg-card border-border flex gap-3 rounded-lg border px-4 py-3.5", className)}
    >
      <Monitor aria-hidden className="text-muted-foreground mt-0.5 size-5 shrink-0" />
      <div className="text-sm">
        <p className="font-semibold">Building needs a computer</p>
        <p className="text-muted-foreground mt-1">
          Assistants are built on a canvas that needs a larger screen. Chat works on your phone:
          sign in to ask your company&apos;s assistants.
        </p>
      </div>
    </div>
  );
}
