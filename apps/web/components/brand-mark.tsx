import { cn } from "@/lib/utils";

/** The brand mark: a short ink line through two ticks into a ring, like a
 *  line with two stops running into an interchange. Drawn in currentColor,
 *  so it follows the text colour in both themes. Decorative by default;
 *  pass `label` when it stands alone. */
export function BrandMark({ className, label }: { className?: string; label?: string }) {
  return (
    <svg
      viewBox="0 0 32 18"
      fill="none"
      stroke="currentColor"
      strokeLinecap="butt"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={cn("h-[18px] w-8 shrink-0", className)}
    >
      <path d="M1 9h20" strokeWidth={3} />
      <path d="M7 4v10M14 4v10" strokeWidth={2.25} />
      <circle cx={25.5} cy={9} r={5} strokeWidth={3} />
    </svg>
  );
}

/** Mark plus the "Assistant Studio" wordmark in condensed 600. */
export function BrandLockup({
  className,
  size = "default",
}: {
  className?: string;
  size?: "default" | "lg" | "display";
}) {
  return (
    <span className={cn("text-foreground inline-flex items-center gap-2.5", className)}>
      <BrandMark
        className={cn(size === "display" && "h-[27px] w-12", size === "lg" && "h-[22px] w-10")}
      />
      <span
        className={cn(
          "font-condensed font-semibold whitespace-nowrap",
          size === "display" ? "text-display" : size === "lg" ? "text-h2" : "text-h3",
        )}
      >
        Assistant Studio
      </span>
    </span>
  );
}
