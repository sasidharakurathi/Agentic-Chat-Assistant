"use client";

import { ChevronDown, ChevronUp, TriangleAlert } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { canChangeData } from "@/components/canvas/station";
import { Lamp } from "@/components/ui/lamp";
import {
  FAMILY_LABEL,
  LineBullet,
  NODE_TYPE_LABEL,
  NODE_TYPE_PLURAL,
  isNodeKind,
  nodeFamily,
  type LineFamily,
  type NodeKind,
} from "@/components/ui/line-bullet";
import type { Graph } from "@/lib/api";

const FAMILIES: LineFamily[] = ["knowledge", "action", "helper"];
const TYPE_ORDER: NodeKind[] = [
  "knowledge_base",
  "data_source",
  "memory",
  "database",
  "tool",
  "mcp_server",
  "subagent",
];

/** The canvas legend (docs/DESIGN.md section 6): a floating layer at the
 *  bottom-left, collapsed to a "Key" button. It lists only the line
 *  families this assistant has, each type with its bullet and count, then
 *  what the lamps and marks mean. Hovering or focusing an entry dims every
 *  other family; pressing it keeps them dimmed until pressed again. */
export function CanvasKey({
  graph,
  onFocusFamily,
}: {
  graph: Graph;
  onFocusFamily: (family: LineFamily | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<LineFamily | null>(null);
  const [pinned, setPinned] = useState<LineFamily | null>(null);
  const panelId = useId();
  const toggle = useRef<HTMLButtonElement>(null);

  const focus = open ? (preview ?? pinned) : null;
  useEffect(() => onFocusFamily(focus), [focus, onFocusFamily]);

  const counts = new Map<NodeKind, number>();
  for (const n of graph.nodes) {
    if (isNodeKind(n.type) && nodeFamily(n.type) !== "main") {
      counts.set(n.type, (counts.get(n.type) ?? 0) + 1);
    }
  }
  const writes = graph.nodes.some(canChangeData);

  const entry = (family: LineFamily) => ({
    "aria-pressed": pinned === family,
    onMouseEnter: () => setPreview(family),
    onMouseLeave: () => setPreview(null),
    onFocus: () => setPreview(family),
    onBlur: () => setPreview(null),
    onClick: () => setPinned((p) => (p === family ? null : family)),
  });
  const row =
    "hover:bg-muted focus-visible:ring-ring flex min-h-8 w-full items-center gap-2 rounded-md px-2 text-left text-sm transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none aria-pressed:bg-muted aria-pressed:font-medium";

  return (
    // The button comes first, so Tab moves from it into the open panel; the
    // panel still opens upward (column-reverse). Escape closes it.
    <div
      className="pointer-events-none flex max-h-full min-h-0 flex-col-reverse items-start justify-start gap-2"
      onKeyDown={(e) => {
        if (e.key !== "Escape" || !open) return;
        e.stopPropagation();
        setOpen(false);
        setPreview(null);
        setPinned(null);
        toggle.current?.focus();
      }}
    >
      <button
        ref={toggle}
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => {
          setOpen((v) => !v);
          setPreview(null);
          setPinned(null);
        }}
        className="border-border bg-card text-label text-foreground hover:bg-muted focus-visible:ring-ring focus-visible:ring-offset-background shadow-float pointer-events-auto inline-flex h-8 items-center gap-1.5 rounded-md border px-3 font-medium transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
      >
        Key
        {open ? (
          <ChevronDown aria-hidden className="size-4" />
        ) : (
          <ChevronUp aria-hidden className="size-4" />
        )}
      </button>
      {open && (
        <section
          id={panelId}
          aria-label="Key"
          className="border-border bg-card shadow-float animate-float-in pointer-events-auto flex min-h-0 w-64 flex-col overflow-y-auto rounded-lg border p-2"
        >
          <ul className="flex flex-col">
            <li>
              <button type="button" className={row} {...entry("main")}>
                <span aria-hidden className="bg-node-main h-[5px] w-4 shrink-0" />
                {FAMILY_LABEL.main}
              </button>
            </li>
          </ul>
          {FAMILIES.map((family) => {
            const types = TYPE_ORDER.filter((t) => nodeFamily(t) === family && counts.has(t));
            if (types.length === 0) return null;
            return (
              <div key={family} className="border-border mt-1 border-t pt-1">
                <h3 className="text-h4 px-2 pt-1 pb-0.5 font-semibold">{FAMILY_LABEL[family]}</h3>
                <ul className="flex flex-col">
                  {types.map((t) => {
                    const n = counts.get(t)!;
                    return (
                      <li key={t}>
                        <button type="button" className={row} {...entry(family)}>
                          <LineBullet type={t} size={16} decorative />
                          <span className="num">
                            {n === 1 ? NODE_TYPE_LABEL[t] : NODE_TYPE_PLURAL[t]}, {n}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
          <ul className="border-border text-muted-foreground mt-1 flex flex-col gap-1 border-t px-2 pt-2 pb-1 text-sm">
            <li className="flex items-center gap-2">
              <Lamp tone="destructive" className="mx-1" />
              Red: a problem to fix
            </li>
            <li className="flex items-center gap-2">
              <Lamp tone="warning" className="mx-1" />
              Ochre: check this
            </li>
            {writes && (
              <li className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="bg-warning text-warning-foreground flex size-4 shrink-0 items-center justify-center rounded-full"
                >
                  <TriangleAlert className="size-2.5" strokeWidth={2.5} />
                </span>
                Can change data
              </li>
            )}
            <li className="flex items-center gap-2">
              <svg aria-hidden width="16" height="6" className="shrink-0">
                <line
                  x1="0"
                  y1="3"
                  x2="16"
                  y2="3"
                  className="stroke-destructive stroke-3 [stroke-dasharray:6_4]"
                />
              </svg>
              Dashed: this connection won&apos;t run
            </li>
          </ul>
        </section>
      )}
    </div>
  );
}
