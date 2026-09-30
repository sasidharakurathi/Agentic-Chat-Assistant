"use client";

import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";

import { NODE_LABEL, STRUCTURAL_NODE_TYPES } from "@/components/canvas/graph-sync";
import { stationName } from "@/components/canvas/station";
import {
  AgentPanel,
  DataSourcePanel,
  DatabasePanel,
  OutputPanel,
  GuardrailsPanel,
  KnowledgeBasePanel,
  MemoryPanel,
  RouterNodePanel,
  SubagentNodePanel,
} from "@/components/config/panels";
import { McpServerNodePanel } from "@/components/config/mcp-node";
import { ToolNodePanel, type ApprovalMode } from "@/components/config/tool-settings";
import { Button } from "@/components/ui/button";
import { LineBullet } from "@/components/ui/line-bullet";
import type { DataSource, DbConnection, GraphNode, McpServer } from "@/lib/api";

/** The selected station's settings: a 360px column on the right of the
 *  canvas that reserves its width while open, so it never covers the
 *  canvas overlays. Below 768px it covers the canvas instead. Structural
 *  nodes compile to the pipeline itself; removing one is a validation
 *  error, not an edit, so the drawer doesn't offer it. */
export function NodeDrawer({
  node,
  models,
  sources,
  databases,
  httpPolicy = "require",
  mcpServers = [],
  mcpDefault = "require",
  agentAssist,
  sharedSubagentModel = "",
  wiredIn = [],
  onPatch,
  onDelete,
  onClose,
}: {
  node: GraphNode | null;
  models: string[];
  sources: DataSource[];
  databases: DbConnection[];
  /** The assistant-wide rule for HTTP writes, so the tool drawer can say
   *  when it overrides the node's own setting. */
  httpPolicy?: ApprovalMode;
  mcpServers?: McpServer[];
  /** The assistant's `mcp_default` rule. */
  mcpDefault?: ApprovalMode;
  /** "Write it for me" under the agent node's system prompt (task 5.5). */
  agentAssist?: ReactNode;
  /** The shared subagent model, which a subagent node follows by default. */
  sharedSubagentModel?: string;
  /** What is wired into the selected node, by label (task 5.10). */
  wiredIn?: string[];
  onPatch: (patch: Record<string, unknown>) => void;
  onDelete?: (id: string) => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const drawer = useRef<HTMLElement>(null);
  // Below 768px the drawer covers the canvas, so focus moves into it when it
  // opens (on the close button); otherwise a keyboard user would carry on
  // through stations hidden underneath. Wider, it sits beside the canvas and
  // focus stays where it was.
  const nodeId = node?.id;
  useEffect(() => {
    if (!nodeId || typeof window.matchMedia !== "function") return;
    if (!window.matchMedia("(max-width: 767.98px)").matches) return;
    drawer.current?.querySelector<HTMLElement>("[data-drawer-close]")?.focus();
  }, [nodeId]);
  if (!node) return null;
  const t = node.type;
  const canDelete = onDelete && !STRUCTURAL_NODE_TYPES.has(t);
  const typeLabel = NODE_LABEL[t] ?? t;

  // The thing itself, by name, where the node points at one: "Shop PG"
  // rather than "Database".
  const named =
    t === "data_source"
      ? sources.find((s) => s.id === String(node.data.data_source_id ?? ""))?.name
      : t === "database"
        ? databases.find((d) => d.id === String(node.data.connection_id ?? ""))?.name
        : t === "mcp_server"
          ? mcpServers.find((s) => s.id === String(node.data.mcp_server_id ?? ""))?.name
          : undefined;
  const title = named ?? stationName(node);
  const subtitle = title !== typeLabel ? typeLabel : null;

  return (
    <aside
      ref={drawer}
      aria-labelledby={titleId}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !e.defaultPrevented) {
          e.stopPropagation();
          onClose();
        }
      }}
      className="border-border bg-card absolute inset-0 z-20 flex flex-col md:static md:z-auto md:w-[360px] md:shrink-0 md:border-l"
    >
      <header className="border-border flex items-center gap-3 border-b px-4 py-3">
        <LineBullet type={t} size={24} decorative />
        <div className="flex min-w-0 flex-1 flex-col">
          {subtitle && <span className="text-small text-muted-foreground">{subtitle}</span>}
          <h2 id={titleId} className="text-h3 truncate font-semibold">
            {title}
          </h2>
        </div>
        <Button
          data-drawer-close
          type="button"
          variant="ghost"
          size="icon"
          aria-label={`Close ${title} settings`}
          title="Close"
          onClick={onClose}
        >
          <X aria-hidden />
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {t === "agent" && (
          <AgentPanel data={node.data} models={models} onChange={onPatch} assist={agentAssist} />
        )}
        {t === "guardrail" && <GuardrailsPanel data={node.data} onChange={onPatch} />}
        {t === "memory" && <MemoryPanel data={node.data} onChange={onPatch} />}
        {t === "knowledge_base" && <KnowledgeBasePanel data={node.data} onChange={onPatch} />}
        {t === "data_source" && (
          <DataSourcePanel
            data={node.data}
            sources={sources.map((src) => ({
              id: src.id,
              name: src.name,
              status: src.status,
            }))}
            onChange={onPatch}
          />
        )}
        {t === "input" && (
          <p className="text-muted-foreground max-w-[60ch] text-sm">
            Every message comes in here. There is nothing to set.
          </p>
        )}
        {t === "output" && <OutputPanel data={node.data} onChange={onPatch} />}
        {t === "tool" && <ToolNodePanel data={node.data} policy={httpPolicy} onChange={onPatch} />}
        {t === "database" && (
          <DatabasePanel
            data={node.data}
            connections={databases.map((d) => ({
              id: d.id,
              name: d.name,
              engine: d.engine,
              write: d.permissions.write,
              permissions: d.permissions,
            }))}
            onChange={onPatch}
          />
        )}
        {t === "mcp_server" && (
          <McpServerNodePanel
            data={node.data}
            server={mcpServers.find((s) => s.id === String(node.data.mcp_server_id ?? ""))}
            assistantDefault={mcpDefault}
            onChange={onPatch}
          />
        )}
        {t === "subagent" && (
          <SubagentNodePanel
            data={node.data}
            models={models}
            sharedModel={sharedSubagentModel}
            wiredIn={wiredIn}
            onChange={onPatch}
          />
        )}
        {t === "router" && <RouterNodePanel data={node.data} models={models} onChange={onPatch} />}
      </div>

      {canDelete && (
        <footer className="border-border flex flex-col gap-1.5 border-t px-4 py-3">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="hover:text-destructive focus-visible:text-destructive w-full"
            onClick={() => onDelete(node.id)}
          >
            Remove from canvas
          </Button>
          <p className="text-small text-muted-foreground max-md:hidden">
            Or press Delete while it&apos;s selected on the canvas.
          </p>
        </footer>
      )}
    </aside>
  );
}
