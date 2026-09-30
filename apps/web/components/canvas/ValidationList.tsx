"use client";

import { ChevronDown, ChevronUp } from "lucide-react";
import { useId, useState } from "react";

import { stationLabel } from "@/components/canvas/graph-sync";
import { plural } from "@/components/canvas/station";
import { Button } from "@/components/ui/button";
import { Lamp } from "@/components/ui/lamp";
import type { Graph, GraphFix, ValidationResult } from "@/lib/api";
import { cn } from "@/lib/utils";

/** "2 problems to fix before you can publish" / "1 thing to check". */
export function validationHeadline(errors: number, warnings: number): string {
  if (errors > 0) return `${plural(errors, "problem", "problems")} to fix before you can publish`;
  return plural(warnings, "thing to check", "things to check");
}

/** Every validation message, readable and clickable (tasks 1.3 / 1.14), with
 *  a one-click fix where there is a safe one (task 5.11).
 *
 *  The API always returned messages; the UI only ever showed counts, plus a
 *  red border on some nodes. A graph-level problem (a duplicate node, a
 *  cycle, a missing agent) had no node to border, so it was invisible apart
 *  from a count. Clicking an item with a node selects it on the canvas.
 *
 *  `floating` draws it as a floating layer for the canvas (the page places
 *  it: docked at the bottom-right, clear of the drawer and the run panel);
 *  otherwise it sits in a page (Settings). `open` / `onOpenChange` let the
 *  page's problem count open it. */
export function ValidationList({
  graph,
  validation,
  onSelectNode,
  onFix,
  floating = true,
  busy = false,
  sourceLabels = {},
  open: openProp,
  onOpenChange,
  id,
  className,
}: {
  graph: Graph;
  validation: ValidationResult;
  onSelectNode?: (id: string) => void;
  /** Apply a suggested fix; the page saves the graph. */
  onFix?: (fix: GraphFix) => void;
  floating?: boolean;
  busy?: boolean;
  /** Names of what data source, database and MCP nodes point at. */
  sourceLabels?: Record<string, string>;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Id for the toggle button, so the page can move focus to the list. */
  id?: string;
  className?: string;
}) {
  const [openState, setOpenState] = useState(true);
  const open = openProp ?? openState;
  const setOpen = (v: boolean) => {
    setOpenState(v);
    onOpenChange?.(v);
  };
  const listId = useId();
  const items = [
    ...validation.errors.map((i) => ({ ...i, level: "error" as const })),
    ...validation.warnings.map((i) => ({ ...i, level: "warning" as const })),
  ];
  if (items.length === 0) return null;
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const name = (nodeId: string) => {
    const n = byId.get(nodeId);
    // What the node points at, by name ("Shop PG"), else its own name.
    return n ? stationLabel(n, sourceLabels) : nodeId;
  };
  const where = (i: (typeof items)[number]) => {
    if (i.node_id) return name(i.node_id);
    if (i.edge) return `Connection from ${name(i.edge[0])} to ${name(i.edge[1])}`;
    return "Whole pipeline";
  };
  const errors = validation.errors.length;
  const warnings = validation.warnings.length;
  const fixable = items.filter((i) => i.fix).length;

  return (
    <section
      aria-label="Problems"
      className={cn(
        "border-border bg-card flex min-h-0 flex-col rounded-lg border",
        floating && "shadow-float pointer-events-auto",
        className,
      )}
    >
      <button
        id={id}
        type="button"
        className="hover:bg-muted focus-visible:ring-ring flex w-full shrink-0 items-start gap-2.5 rounded-lg px-3 py-2.5 text-left transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => setOpen(!open)}
      >
        <Lamp tone={errors > 0 ? "destructive" : "warning"} className="mt-1.5" />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="text-sm font-medium">{validationHeadline(errors, warnings)}</span>
          {errors > 0 && warnings > 0 && (
            <span className="text-small text-muted-foreground">
              {plural(warnings, "thing to check", "things to check")}
            </span>
          )}
          {fixable > 0 && onFix && (
            <span className="text-small text-muted-foreground">
              {fixable === 1 ? "1 has a suggested fix" : `${fixable} have a suggested fix`}
            </span>
          )}
        </span>
        {open ? (
          <ChevronUp aria-hidden className="text-muted-foreground mt-0.5 size-4 shrink-0" />
        ) : (
          <ChevronDown aria-hidden className="text-muted-foreground mt-0.5 size-4 shrink-0" />
        )}
      </button>
      {open && (
        <ul
          id={listId}
          className={cn(
            "border-border divide-border min-h-0 divide-y overflow-y-auto border-t",
            floating && "max-h-72",
          )}
        >
          {items.map((i, k) => {
            const body = (
              <>
                <span className="flex items-center gap-2">
                  <Lamp
                    tone={i.level === "error" ? "destructive" : "warning"}
                    label={i.level === "error" ? "Problem" : "Check this"}
                  />
                  <span className="text-small text-muted-foreground truncate">{where(i)}</span>
                </span>
                <span className="mt-1 block text-sm">{i.message}</span>
              </>
            );
            return (
              <li key={`${i.level}-${i.code}-${k}`} className="px-3 py-2.5">
                {i.node_id && onSelectNode ? (
                  <button
                    type="button"
                    onClick={() => onSelectNode(i.node_id!)}
                    className="hover:bg-muted focus-visible:ring-ring -mx-1.5 -my-1 block w-[calc(100%+0.75rem)] rounded-md px-1.5 py-1 text-left transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none"
                  >
                    {body}
                  </button>
                ) : (
                  <div>{body}</div>
                )}
                {i.fix && onFix && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="mt-2"
                    disabled={busy}
                    onClick={() => onFix(i.fix!)}
                  >
                    {i.fix.label}
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
