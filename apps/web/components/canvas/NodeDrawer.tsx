"use client";

import { NODE_LABEL } from "@/components/canvas/graph-sync";
import { AgentPanel, GuardrailsPanel, MemoryPanel } from "@/components/config/panels";
import type { GraphNode } from "@/lib/api";

export function NodeDrawer({
  node,
  models,
  onPatch,
  onClose,
}: {
  node: GraphNode | null;
  models: string[];
  onPatch: (patch: Record<string, unknown>) => void;
  onClose: () => void;
}) {
  if (!node) return null;
  const t = node.type;
  return (
    <div className="border-border w-80 shrink-0 overflow-auto border-l p-4">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold">{NODE_LABEL[t] ?? t}</h3>
        <button
          onClick={onClose}
          className="text-muted-foreground hover:text-foreground text-lg leading-none"
        >
          &times;
        </button>
      </div>
      {t === "agent" && <AgentPanel data={node.data} models={models} onChange={onPatch} />}
      {t === "guardrail" && <GuardrailsPanel data={node.data} onChange={onPatch} />}
      {t === "memory" && <MemoryPanel data={node.data} onChange={onPatch} />}
      {(t === "input" || t === "output") && (
        <p className="text-muted-foreground text-sm">Nothing to configure on this node.</p>
      )}
      {t === "tool" && (
        <p className="text-muted-foreground text-sm">
          Turn tools on and off in the <strong>Panels</strong> tab.
        </p>
      )}
      {["knowledge_base", "database", "mcp_server", "subagent", "router", "data_source"].includes(
        t,
      ) && (
        <p className="text-muted-foreground text-sm">
          This node type gets an editor in a later phase.
        </p>
      )}
    </div>
  );
}
