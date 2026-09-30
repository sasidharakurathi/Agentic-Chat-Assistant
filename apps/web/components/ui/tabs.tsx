"use client";

import { Fragment, useRef, type KeyboardEvent } from "react";

import { cn } from "@/lib/utils";

export type TabOption<T extends string> = {
  value: T;
  label: string;
  /** Draw a short vertical rule before this tab, to group related tabs. */
  divider?: boolean;
};

/** Ids that tie a tab to its panel, from the `idBase` given to `<Tabs>`. */
export function tabIds(idBase: string, value: string) {
  return { tab: `${idBase}-tab-${value}`, panel: `${idBase}-panel-${value}` };
}

/** Spread on the element that shows the selected tab's content, with the
 *  same `idBase` as `<Tabs>`, to link the two for assistive technology. */
export function tabPanelProps(idBase: string, value: string) {
  const ids = tabIds(idBase, value);
  return { id: ids.panel, role: "tabpanel" as const, "aria-labelledby": ids.tab, tabIndex: 0 };
}

/** Underline tabs with the ARIA tabs pattern (task 0.4): `tablist` / `tab`
 *  roles and `aria-selected`, one tab stop for the whole strip (roving
 *  tabindex), and arrow keys / Home / End to move between tabs. 40px tall,
 *  14/500 muted; the selected tab is ink with a 2px bar underneath. Pass
 *  `idBase` (and `tabPanelProps` on the panel) to link tabs and panels. */
export function Tabs<T extends string>({
  value,
  onChange,
  options,
  className,
  label,
  idBase,
}: {
  value: T;
  onChange: (v: T) => void;
  options: TabOption<T>[];
  className?: string;
  /** Accessible name for the strip. */
  label?: string;
  /** Prefix for tab and panel ids; enables `aria-controls`. */
  idBase?: string;
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
      aria-orientation="horizontal"
      onKeyDown={onKeyDown}
      className={cn(
        "flex max-w-full min-w-0 [scrollbar-width:none] items-stretch overflow-x-auto",
        className,
      )}
    >
      {options.map((o, i) => {
        const selected = value === o.value;
        const ids = idBase ? tabIds(idBase, o.value) : null;
        return (
          <Fragment key={o.value}>
            {o.divider && i > 0 && (
              <span aria-hidden className="bg-border mx-2 my-auto h-4 w-px shrink-0" />
            )}
            <button
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="tab"
              id={ids?.tab}
              aria-controls={ids?.panel}
              aria-selected={selected}
              tabIndex={selected ? 0 : -1}
              onClick={() => onChange(o.value)}
              className={cn(
                "focus-visible:ring-ring relative inline-flex h-10 shrink-0 items-center rounded-sm px-3 text-sm font-medium whitespace-nowrap transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset",
                "after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:rounded-full",
                selected
                  ? "text-foreground after:bg-primary"
                  : "text-muted-foreground hover:text-foreground after:bg-transparent",
              )}
            >
              {o.label}
            </button>
          </Fragment>
        );
      })}
    </div>
  );
}
