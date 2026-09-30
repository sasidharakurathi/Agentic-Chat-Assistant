"use client";

import { useState } from "react";

import { BUILTIN_TOOLS, TOOL_LABEL } from "@/components/config/tool-settings";
import { Button } from "@/components/ui/button";
import type { DataSource, DbConnection, McpServer } from "@/lib/api";
import { cn } from "@/lib/utils";

const SUBAGENT_ROLES = [
  { key: "retrieval", label: "Retrieval subagent" },
  { key: "sql", label: "SQL subagent" },
  { key: "research", label: "Research subagent" },
] as const;

/** What the canvas can add today.
 *
 *  The RAG node types (Phase 2), `database` (Phase 3), the built-in
 *  `tool`s and `mcp_server`s (Phase 4), and `subagent`s and the `router`
 *  (task 5.10).
 */
export function NodePalette({
  sources,
  databases,
  hasKnowledgeBase,
  toolsOnCanvas = [],
  mcpServers = [],
  onAddKnowledgeBase,
  onAddDataSource,
  onAddDatabase,
  onAddTool,
  onAddMcpServer,
  subagentsOnCanvas = [],
  hasRouter = false,
  onAddSubagent,
  onAddRouter,
}: {
  sources: DataSource[];
  databases: DbConnection[];
  hasKnowledgeBase: boolean;
  /** Tool keys already on the canvas; a second node for one would be
   *  rejected by validation, so those are shown as added instead. */
  toolsOnCanvas?: string[];
  onAddKnowledgeBase: () => void;
  onAddDataSource: (dataSourceId: string) => void;
  onAddDatabase: (connectionId: string) => void;
  onAddTool?: (key: string) => void;
  /** Registered MCP servers (the MCP tab); one node each. */
  mcpServers?: McpServer[];
  onAddMcpServer?: (serverId: string) => void;
  /** Subagent roles already on the canvas: one node each. */
  subagentsOnCanvas?: string[];
  hasRouter?: boolean;
  onAddSubagent?: (role: string) => void;
  onAddRouter?: () => void;
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

          {onAddTool && (
            <div className="flex flex-col gap-1.5">
              <p className="text-muted-foreground text-xs font-medium">Tools</p>
              {BUILTIN_TOOLS.map((key) => {
                const added = toolsOnCanvas.includes(key);
                return (
                  <button
                    key={key}
                    onClick={() => onAddTool(key)}
                    className="border-border hover:bg-muted flex items-center gap-2 rounded-md border px-2 py-1.5 text-left text-xs"
                  >
                    <span className="bg-node-tool inline-block h-2 w-2 shrink-0 rounded-full" />
                    <span className="truncate">{TOOL_LABEL[key]}</span>
                    {added && <span className="text-muted-foreground ml-auto shrink-0">added</span>}
                  </button>
                );
              })}
            </div>
          )}

          {onAddMcpServer && (
            <div className="flex flex-col gap-1.5">
              <p className="text-muted-foreground text-xs font-medium">MCP servers</p>
              {mcpServers.length === 0 && (
                <p className="text-muted-foreground text-xs">
                  None yet — add one in the <strong>MCP</strong> tab.
                </p>
              )}
              {mcpServers.map((server) => (
                <button
                  key={server.id}
                  onClick={() => onAddMcpServer(server.id)}
                  className="border-border hover:bg-muted flex items-center gap-2 rounded-md border px-2 py-1.5 text-left text-xs"
                >
                  <span className="bg-node-mcp inline-block h-2 w-2 shrink-0 rounded-full" />
                  <span className="truncate">{server.name}</span>
                  <span className="text-muted-foreground ml-auto shrink-0">
                    {server.tools.length} tools
                  </span>
                </button>
              ))}
            </div>
          )}

          {onAddSubagent && (
            <div className="flex flex-col gap-1.5">
              <p className="text-muted-foreground text-xs font-medium">Subagents</p>
              {SUBAGENT_ROLES.map(({ key, label }) => {
                const added = subagentsOnCanvas.includes(key);
                return (
                  <button
                    key={key}
                    onClick={() => onAddSubagent(key)}
                    className="border-border hover:bg-muted flex items-center gap-2 rounded-md border px-2 py-1.5 text-left text-xs"
                  >
                    <span className="bg-node-subagent inline-block h-2 w-2 shrink-0 rounded-full" />
                    <span className="truncate">{label}</span>
                    {added && <span className="text-muted-foreground ml-auto shrink-0">added</span>}
                  </button>
                );
              })}
              <p className="text-muted-foreground text-xs">
                Wire a database, knowledge base or tool into a subagent to make it that
                subagent&apos;s alone.
              </p>
            </div>
          )}

          {onAddRouter && (
            <Button
              size="sm"
              variant="outline"
              className="justify-start"
              disabled={hasRouter}
              onClick={onAddRouter}
            >
              <span className="bg-node-router mr-2 inline-block h-2 w-2 rounded-full" />
              Router{hasRouter ? " (added)" : ""}
            </Button>
          )}

          <p className="text-muted-foreground text-xs">
            Drag from a node&apos;s right edge to its target to wire it up.
          </p>
        </div>
      )}
    </div>
  );
}
