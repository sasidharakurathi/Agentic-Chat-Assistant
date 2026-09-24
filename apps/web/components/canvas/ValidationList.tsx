"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import type { Graph, ValidationResult } from "@/lib/api";
import { NODE_LABEL } from "@/components/canvas/graph-sync";

/** Every validation message, readable and clickable (tasks 1.3 / 1.14).
 *
 *  The API always returned messages; the UI only ever showed counts, plus a
 *  red border on some nodes. A graph-level error (a duplicate node, a cycle,
 *  a missing agent) had no node to border, so it was invisible apart from
 *  "1 error(s)". Clicking an item with a node selects it on the canvas. */
export function ValidationList({
  graph,
  validation,
  onSelectNode,
}: {
  graph: Graph;
  validation: ValidationResult;
  onSelectNode: (id: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const items = [
    ...validation.errors.map((i) => ({ ...i, level: "error" as const })),
    ...validation.warnings.map((i) => ({ ...i, level: "warning" as const })),
  ];
  if (items.length === 0) return null;
  const typeOf = new Map(graph.nodes.map((n) => [n.id, n.type]));
  const where = (i: (typeof items)[number]) => {
    if (i.node_id) return NODE_LABEL[typeOf.get(i.node_id) ?? ""] ?? i.node_id;
    if (i.edge) {
      const [s, t] = i.edge;
      return `${NODE_LABEL[typeOf.get(s) ?? ""] ?? s} → ${NODE_LABEL[typeOf.get(t) ?? ""] ?? t}`;
    }
    return "Pipeline";
  };

  return (
    <div className="border-border bg-card/95 absolute right-3 bottom-3 z-10 w-80 rounded-lg border text-xs shadow-md backdrop-blur">
      <button
        type="button"
        className="flex w-full items-center justify-between px-3 py-2 font-medium"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span>
          {validation.errors.length > 0
            ? `${validation.errors.length} error(s) block publishing`
            : `${validation.warnings.length} warning(s)`}
        </span>
        <span className="text-muted-foreground">{open ? "Hide" : "Show"}</span>
      </button>
      {open && (
        <ul className="border-border max-h-64 divide-y overflow-auto border-t">
          {items.map((i, k) => (
            <li key={`${i.level}-${i.code}-${k}`}>
              <button
                type="button"
                disabled={!i.node_id}
                onClick={() => i.node_id && onSelectNode(i.node_id)}
                className="hover:bg-muted/60 w-full px-3 py-2 text-left disabled:cursor-default"
              >
                <div className="flex items-center gap-2">
                  <Badge variant={i.level === "error" ? "destructive" : "warning"}>{i.level}</Badge>
                  <span className="text-muted-foreground truncate">{where(i)}</span>
                </div>
                <p className="mt-1">{i.message}</p>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
