"use client";

import { ChevronDown, ChevronUp, Plus } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { ENGINE_NAME, SOURCE_STATUS, plural } from "@/components/canvas/station";
import { BUILTIN_TOOLS, TOOL_LABEL } from "@/components/config/tool-settings";
import { buttonVariants } from "@/components/ui/button";
import { LineBullet } from "@/components/ui/line-bullet";
import type { DataSource, DbConnection, McpServer } from "@/lib/api";
import { cn } from "@/lib/utils";

const SUBAGENT_ROLES = [
  { key: "retrieval", label: "Retrieval subagent" },
  { key: "sql", label: "SQL subagent" },
  { key: "research", label: "Research subagent" },
] as const;

/** Where the palette can send someone to create what it lists. */
export type PaletteDestination = "sources" | "databases" | "mcp";

/** "Add capability": what the canvas can add (docs/DESIGN.md section 7). A
 *  floating layer at the top-left of the canvas, collapsed to its button.
 *  Each row is the capability's line bullet and its name; a click adds it
 *  (or selects it if it is already there, which the row says).
 *
 *  The RAG node types (Phase 2), `database` (Phase 3), the built-in
 *  `tool`s and `mcp_server`s (Phase 4), and `subagent`s and the `router`
 *  (task 5.10). */
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
  onGoTo,
  className,
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
  /** Registered MCP servers (the MCP servers tab); one node each. */
  mcpServers?: McpServer[];
  onAddMcpServer?: (serverId: string) => void;
  /** Subagent roles already on the canvas: one node each. */
  subagentsOnCanvas?: string[];
  hasRouter?: boolean;
  onAddSubagent?: (role: string) => void;
  onAddRouter?: () => void;
  /** Open the tab where a missing source, database or server is created. */
  onGoTo?: (where: PaletteDestination) => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();

  const empty = (what: string, tab: string, where: PaletteDestination) => (
    <p className="text-small text-muted-foreground px-2 py-1">
      No {what} yet.{" "}
      {onGoTo ? (
        <button
          type="button"
          onClick={() => onGoTo(where)}
          className={buttonVariants({ variant: "link", className: "text-small" })}
        >
          Add one in {tab}
        </button>
      ) : (
        <>Add one in the {tab} tab.</>
      )}
    </p>
  );

  return (
    <div
      // Lets the page keep other overlays clear of the open palette on
      // narrow screens.
      data-palette-open={open || undefined}
      className={cn(
        "border-border bg-card shadow-float pointer-events-auto flex max-h-full min-h-0 w-68 max-w-full flex-col rounded-lg border",
        className,
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={open ? bodyId : undefined}
        className="text-label hover:bg-muted focus-visible:ring-ring flex h-9 w-full shrink-0 items-center gap-2 rounded-lg px-3 font-medium transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none"
      >
        <Plus aria-hidden className="size-4" />
        <span className="flex-1 text-left">Add capability</span>
        {open ? (
          <ChevronUp aria-hidden className="text-muted-foreground size-4" />
        ) : (
          <ChevronDown aria-hidden className="text-muted-foreground size-4" />
        )}
      </button>

      {open && (
        <div
          id={bodyId}
          className="border-border animate-float-in flex min-h-0 flex-col gap-3 overflow-y-auto border-t p-2"
        >
          <Group title="Knowledge">
            <Item
              type="knowledge_base"
              label="Knowledge base"
              meta={hasKnowledgeBase ? "Added" : undefined}
              disabled={hasKnowledgeBase}
              onClick={onAddKnowledgeBase}
            />
            {hasKnowledgeBase && (
              <p className="text-small text-muted-foreground px-2">
                This assistant already has a knowledge base. Add sources to it instead.
              </p>
            )}
          </Group>

          <Group title="Data sources">
            {sources.length === 0 && empty("sources", "Sources", "sources")}
            {sources.map((src) => (
              <Item
                key={src.id}
                type="data_source"
                label={src.name}
                meta={SOURCE_STATUS[src.status]}
                onClick={() => onAddDataSource(src.id)}
              />
            ))}
          </Group>

          <Group title="Databases">
            {databases.length === 0 && empty("databases", "Databases", "databases")}
            {databases.map((db) => (
              <Item
                key={db.id}
                type="database"
                label={db.name}
                meta={ENGINE_NAME[db.engine] ?? db.engine}
                onClick={() => onAddDatabase(db.id)}
              />
            ))}
          </Group>

          {onAddTool && (
            <Group title="Tools">
              {BUILTIN_TOOLS.map((key) => (
                <Item
                  key={key}
                  type="tool"
                  label={TOOL_LABEL[key]}
                  meta={toolsOnCanvas.includes(key) ? "Added" : undefined}
                  onClick={() => onAddTool(key)}
                />
              ))}
            </Group>
          )}

          {onAddMcpServer && (
            <Group title="MCP servers">
              {mcpServers.length === 0 && empty("MCP servers", "MCP servers", "mcp")}
              {mcpServers.map((server) => (
                <Item
                  key={server.id}
                  type="mcp_server"
                  label={server.name}
                  hollow={!server.enabled}
                  meta={
                    server.enabled ? plural(server.tools.length, "tool", "tools") : "Switched off"
                  }
                  onClick={() => onAddMcpServer(server.id)}
                />
              ))}
            </Group>
          )}

          {onAddSubagent && (
            <Group
              title="Subagents"
              hint="Connect a database, knowledge base or tool to a subagent to make it that subagent's alone."
            >
              {SUBAGENT_ROLES.map(({ key, label }) => (
                <Item
                  key={key}
                  type="subagent"
                  label={label}
                  meta={subagentsOnCanvas.includes(key) ? "Added" : undefined}
                  onClick={() => onAddSubagent(key)}
                />
              ))}
            </Group>
          )}

          {onAddRouter && (
            <Group title="Pipeline">
              <Item
                type="router"
                label="Router"
                meta={hasRouter ? "Added" : undefined}
                disabled={hasRouter}
                onClick={onAddRouter}
              />
            </Group>
          )}

          <p className="text-small text-muted-foreground border-border border-t px-2 pt-2">
            To connect two things on the canvas, drag from the dot on the right of one to the other.
          </p>
        </div>
      )}
    </div>
  );
}

function Group({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-0.5">
      <h3 id={id} className="text-h4 px-2 pb-0.5 font-semibold">
        {title}
      </h3>
      {children}
      {hint && <p className="text-small text-muted-foreground px-2 pt-1">{hint}</p>}
    </section>
  );
}

function Item({
  type,
  label,
  meta,
  hollow = false,
  disabled = false,
  onClick,
}: {
  type: string;
  label: string;
  meta?: string;
  hollow?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="hover:bg-muted focus-visible:ring-ring flex min-h-9 w-full items-center gap-2 rounded-md px-2 text-left text-sm transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:outline-none disabled:cursor-default disabled:opacity-60 disabled:hover:bg-transparent"
    >
      <LineBullet type={type} size={20} hollow={hollow} decorative />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {meta && <span className="text-small text-muted-foreground shrink-0">{meta}</span>}
    </button>
  );
}
