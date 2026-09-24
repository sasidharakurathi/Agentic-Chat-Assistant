"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { DataSource, DbConnection } from "@/lib/api";
import { cn } from "@/lib/utils";

/** What the canvas can add today.
 *
 *  The RAG node types (Phase 2) plus `database` (Phase 3). `tool`,
 *  `mcp_server` and `subagent` are still absent: they reference things that
 *  do not exist yet (an MCP server registration, a user-composed subagent),
 *  and offering them would let someone build a graph that can never
 *  validate. They arrive with their own phases.
 */
export function NodePalette({
  sources,
  databases,
  hasKnowledgeBase,
  onAddKnowledgeBase,
  onAddDataSource,
  onAddDatabase,
}: {
  sources: DataSource[];
  databases: DbConnection[];
  hasKnowledgeBase: boolean;
  onAddKnowledgeBase: () => void;
  onAddDataSource: (dataSourceId: string) => void;
  onAddDatabase: (connectionId: string) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border-border bg-card absolute top-3 left-3 z-10 w-60 rounded-lg border shadow-sm">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-sm font-medium"
        aria-expanded={open}
      >
        Add node
        <span className="text-muted-foreground">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="border-border flex flex-col gap-3 border-t p-3">
          <div className="flex flex-col gap-1.5">
            <Button
              size="sm"
              variant="outline"
              className="justify-start"
              disabled={hasKnowledgeBase}
              onClick={onAddKnowledgeBase}
            >
              <span className="bg-node-kb mr-2 inline-block h-2 w-2 rounded-full" />
              Knowledge base
            </Button>
            {hasKnowledgeBase && (
              <p className="text-muted-foreground text-xs">
                Only one knowledge base per assistant — a second one would be ignored.
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <p className="text-muted-foreground text-xs font-medium">Data sources</p>
            {sources.length === 0 && (
              <p className="text-muted-foreground text-xs">
                None yet — add one in the <strong>Sources</strong> tab.
              </p>
            )}
            {sources.map((src) => (
              <button
                key={src.id}
                onClick={() => onAddDataSource(src.id)}
                className={cn(
                  "border-border hover:bg-muted flex items-center gap-2 rounded-md border px-2 py-1.5 text-left text-xs",
                )}
              >
                <span className="bg-node-datasource inline-block h-2 w-2 shrink-0 rounded-full" />
                <span className="truncate">{src.name}</span>
                {src.status !== "ready" && (
                  <span className="text-muted-foreground ml-auto shrink-0">{src.status}</span>
                )}
              </button>
            ))}
          </div>

          <div className="flex flex-col gap-1.5">
            <p className="text-muted-foreground text-xs font-medium">Databases</p>
            {databases.length === 0 && (
              <p className="text-muted-foreground text-xs">
                None yet — add one in the <strong>Databases</strong> tab.
              </p>
            )}
            {databases.map((db) => (
              <button
                key={db.id}
                onClick={() => onAddDatabase(db.id)}
                className="border-border hover:bg-muted flex items-center gap-2 rounded-md border px-2 py-1.5 text-left text-xs"
              >
                <span className="bg-node-database inline-block h-2 w-2 shrink-0 rounded-full" />
                <span className="truncate">{db.name}</span>
                <span className="text-muted-foreground ml-auto shrink-0">{db.engine}</span>
              </button>
            ))}
          </div>

          <p className="text-muted-foreground text-xs">
            Drag from a node&apos;s right edge to its target to wire it up.
          </p>
        </div>
      )}
    </div>
  );
}
