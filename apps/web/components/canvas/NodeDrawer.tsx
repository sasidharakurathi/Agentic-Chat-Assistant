"use client";

import type { ReactNode } from "react";

import { STRUCTURAL_NODE_TYPES } from "@/components/canvas/graph-sync";
import { NODE_LABEL } from "@/components/canvas/graph-sync";
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
import { TOOL_LABEL, ToolNodePanel, type ApprovalMode } from "@/components/config/tool-settings";
import { Button } from "@/components/ui/button";
import type { DataSource, DbConnection, GraphNode, McpServer } from "@/lib/api";

/** Structural nodes compile to the pipeline itself; removing one is a
 *  validation error, not an edit, so the drawer doesn't offer it. */

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
  if (!node) return null;
  const t = node.type;
  const canDelete = onDelete && !STRUCTURAL_NODE_TYPES.has(t);

  return (
    <div className="border-border flex w-80 shrink-0 flex-col overflow-auto border-l p-4">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold">
          {NODE_LABEL[t] ?? t}
          {t === "tool" && TOOL_LABEL[String(node.data.key)] && (
            <span className="text-muted-foreground font-normal">
              {" "}
              · {TOOL_LABEL[String(node.data.key)]}
            </span>
          )}
        </h3>
        <button
          onClick={onClose}
          className="text-muted-foreground hover:text-foreground text-lg leading-none"
        >
          &times;
        </button>
      </div>

      <div className="flex-1">
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
          <p className="text-muted-foreground text-sm">Nothing to configure on this node.</p>
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
        <div className="border-border mt-4 border-t pt-4">
          <Button
            size="sm"
            variant="outline"
            className="text-destructive w-full"
            onClick={() => onDelete(node.id)}
          >
            Remove node
          </Button>
        </div>
      )}
    </div>
  );
}
