"use client";

import type { LucideIcon } from "lucide-react";
import { useRef, type KeyboardEvent } from "react";

import { cn } from "@/lib/utils";

export type SegmentedOption<T extends string> = {
  value: T;
  label: string;
  /** A 16px lucide icon shown before the label. */
  icon?: LucideIcon;
};

/** A small single choice (period pickers, mode choices; docs/DESIGN.md
 *  section 5). Same props shape as `Tabs`, but a `radiogroup` of `radio`
 *  items: one tab stop, arrow keys / Home / End move and select. A bordered
 *  group, radius 6, with the chosen segment filled ink. `iconOnly` keeps
 *  the labels for screen readers and tooltips only. */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  className,
  label,
  iconOnly = false,
  size = "default",
}: {
  value: T;
  onChange: (v: T) => void;
  options: SegmentedOption<T>[];
  className?: string;
  /** Accessible name for the group. */
  label?: string;
  iconOnly?: boolean;
  size?: "default" | "sm";
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const current = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  );

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = current;
    const to =
      e.key === "ArrowRight" || e.key === "ArrowDown"
        ? (i + 1) % options.length
        : e.key === "ArrowLeft" || e.key === "ArrowUp"
          ? (i - 1 + options.length) % options.length
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? options.length - 1
              : null;
    if (to === null) return;
    e.preventDefault();
    onChange(options[to].value);
    refs.current[to]?.focus();
  };

  return (
    <div
      role="radiogroup"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={cn(
        "border-field-border bg-card inline-flex max-w-full items-stretch gap-0.5 rounded-md border p-0.5",
        className,
      )}
    >
      {options.map((o, i) => {
        const selected = i === current;
        const Icon = o.icon;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={iconOnly ? o.label : undefined}
            title={iconOnly ? o.label : undefined}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(o.value)}
            className={cn(
              "focus-visible:ring-ring focus-visible:ring-offset-card inline-flex min-w-0 flex-auto items-center justify-center rounded-[4px] font-medium whitespace-nowrap transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:outline-none",
              size === "sm" ? "text-small h-6 gap-1 px-1.5" : "text-label h-7 gap-1.5 px-2.5",
              iconOnly && (size === "sm" ? "w-6 px-0" : "w-7 px-0"),
              selected
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {Icon && (
              <Icon aria-hidden className={cn("shrink-0", size === "sm" ? "size-3.5" : "size-4")} />
            )}
            {!iconOnly && <span className="truncate">{o.label}</span>}
          </button>
        );
      })}
    </div>
  );
}
