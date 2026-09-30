"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { Segmented } from "@/components/ui/segmented";
import { cn } from "@/lib/utils";

const ORDER = ["system", "light", "dark"] as const;
type ThemeChoice = (typeof ORDER)[number];
const LABEL = { system: "System theme", light: "Light theme", dark: "Dark theme" };
const SHORT = { system: "System", light: "Light", dark: "Dark" };
const ICON = { system: Monitor, light: Sun, dark: Moon };

/** The stored theme is only known in the browser; report "system" on the
 *  server and during the first client render so hydration matches. */
function useThemeChoice() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const current = (mounted && ORDER.includes(theme as never) ? theme : "system") as ThemeChoice;
  return { current, setTheme };
}

/** Cycles system → light → dark (task 0.4). The tokens and next-themes were
 *  wired from the start; nothing let a user choose. Used where there is only
 *  room for one icon (the collapsed rail); `iconOnly` hides the text. */
export function ThemeToggle({
  className,
  iconOnly = false,
}: {
  className?: string;
  iconOnly?: boolean;
}) {
  const { current, setTheme } = useThemeChoice();
  const next = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length];
  const Icon = ICON[current];
  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      className={cn(
        "text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-ring text-label inline-flex h-8 items-center gap-2 rounded-md px-2 font-medium transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none",
        iconOnly && "w-8 justify-center px-0",
        className,
      )}
      aria-label={`${LABEL[current]}. Switch to ${LABEL[next].toLowerCase()}`}
      title={`Switch to ${LABEL[next].toLowerCase()}`}
    >
      <Icon className="size-4" aria-hidden />
      {!iconOnly && LABEL[current]}
    </button>
  );
}

/** The theme as a three-way choice (System, Light, Dark), for the rail. */
export function ThemeSegmented({ className }: { className?: string }) {
  const { current, setTheme } = useThemeChoice();
  return (
    <Segmented
      label="Theme"
      size="sm"
      value={current}
      onChange={(v) => setTheme(v)}
      options={ORDER.map((v) => ({ value: v, label: SHORT[v], icon: ICON[v] }))}
      className={className}
    />
  );
}
