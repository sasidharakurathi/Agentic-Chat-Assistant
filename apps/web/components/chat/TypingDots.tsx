import { cn } from "@/lib/utils";

/** Three dots rising and fading in turn: the assistant is working and
 *  nothing has streamed yet. Announced once to screen readers; still under
 *  `prefers-reduced-motion` (see `.typing-dot` in globals.css). */
export function TypingDots({
  label = "Working",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <span
      role="status"
      aria-label={label}
      className={cn("inline-flex items-center gap-1 py-1", className)}
    >
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          aria-hidden
          className="typing-dot bg-muted-foreground inline-block size-1.5 rounded-full"
          style={{ animationDelay: `${i * 160}ms` }}
        />
      ))}
    </span>
  );
}

/** A small pulsing dot with a word, for "this turn is still running". */
export function RunningBadge() {
  return (
    <span className="text-primary ml-2 inline-flex items-center gap-1.5 text-xs font-medium">
      <span aria-hidden className="running-pulse bg-primary inline-block size-1.5 rounded-full" />
      running
    </span>
  );
}
