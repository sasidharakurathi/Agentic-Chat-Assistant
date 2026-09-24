"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

const ORDER = ["system", "light", "dark"] as const;
const LABEL = { system: "System theme", light: "Light theme", dark: "Dark theme" };
const ICON = { system: Monitor, light: Sun, dark: Moon };

/** Cycles system → light → dark (task 0.4). The tokens and next-themes were
 *  wired from the start; nothing let a user choose. */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  // The stored theme is only known in the browser; render a stable
  // placeholder on the server so hydration matches.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const current = (
    mounted && ORDER.includes(theme as never) ? theme : "system"
  ) as (typeof ORDER)[number];
  const next = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length];
  const Icon = ICON[current];
  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      className="text-muted-foreground hover:text-foreground flex items-center gap-2 rounded-md px-2 py-1 text-xs"
      aria-label={`${LABEL[current]}. Switch to ${LABEL[next].toLowerCase()}`}
      title={`Switch to ${LABEL[next].toLowerCase()}`}
    >
      <Icon className="size-3.5" aria-hidden />
      {LABEL[current]}
    </button>
  );
}
