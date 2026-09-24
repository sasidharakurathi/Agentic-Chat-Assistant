"use client";

import { useRef, type KeyboardEvent } from "react";

import { cn } from "@/lib/utils";

/** A tab strip with the ARIA tabs pattern (task 0.4): `tablist` / `tab`
 *  roles and `aria-selected`, one tab stop for the whole strip (roving
 *  tabindex), and arrow keys / Home / End to move between tabs. It used to
 *  be a group of plain buttons, which a screen reader announced as
 *  unrelated buttons and which took one Tab press each. */
export function Tabs<T extends string>({
  value,
  onChange,
  options,
  className,
  label,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  className?: string;
  /** Accessible name for the strip. */
  label?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = options.findIndex((o) => o.value === value);
    const to =
      e.key === "ArrowRight"
        ? (i + 1) % options.length
        : e.key === "ArrowLeft"
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
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={cn("bg-muted inline-flex rounded-md p-0.5", className)}
    >
      {options.map((o, i) => {
        const selected = value === o.value;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="tab"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(o.value)}
            className={cn(
              "rounded px-3 py-1 text-sm font-medium transition-colors",
              selected
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
