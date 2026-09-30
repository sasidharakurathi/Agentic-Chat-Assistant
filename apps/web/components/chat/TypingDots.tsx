import { Lamp } from "@/components/ui/lamp";
import { cn } from "@/lib/utils";

/** Three dots rising and fading in turn, where the answer will appear: the
 *  assistant is working and nothing has streamed yet. Announced once to
 *  screen readers; still under `prefers-reduced-motion` (see `.typing-dot`
 *  in globals.css). */
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
      className={cn("inline-flex h-6 items-center gap-1", className)}
    >
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          aria-hidden
          className={cn(
            "typing-dot bg-muted-foreground inline-block size-1.5 rounded-full",
            i === 1 && "[animation-delay:160ms]",
            i === 2 && "[animation-delay:320ms]",
          )}
        />
      ))}
    </span>
  );
}

/** The running lamp beside a word, for "an answer is being written". It
 *  pulses while live and holds still under reduced motion; the word says
 *  the same thing either way. It carries no live region of its own: put it
 *  inside one that is always in the page (see the chat composer), since a
 *  region inserted together with its text is often not announced. */
export function RunningBadge({
  label = "Answering…",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "text-small text-muted-foreground inline-flex items-center gap-2 font-medium",
        className,
      )}
    >
      <Lamp tone="neutral" live />
      {label}
    </span>
  );
}
