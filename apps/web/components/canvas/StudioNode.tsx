import { Handle, Position, type NodeProps } from "@xyflow/react";

import { NODE_LABEL } from "@/components/canvas/graph-sync";
import { cn } from "@/lib/utils";

// One deliberate categorical family (see globals.css --node-*), not
// assorted named Tailwind colors — each node type gets a fixed hue from
// the same matched lightness/chroma scale, rotated off the primary.
const ACCENT: Record<string, string> = {
  agent: "border-node-agent",
  guardrail: "border-node-guardrail",
  memory: "border-node-memory",
  knowledge_base: "border-node-kb",
  database: "border-node-database",
  tool: "border-node-tool",
  mcp_server: "border-node-mcp",
  subagent: "border-node-subagent",
};

export function StudioNode({ data, selected }: NodeProps) {
  const node = (data as { node: { type: string; data: Record<string, unknown> } }).node;
  const invalid = (data as { invalid?: boolean }).invalid;
  const t = node.type;
  const showTarget = t !== "input";
  const showSource = t !== "output";

  const subtitle =
    t === "agent"
      ? String((node.data.models as { main?: { model?: string } })?.main?.model ?? "")
      : t === "tool"
        ? String(node.data.key ?? "")
        : t === "subagent"
          ? String(node.data.role ?? "")
          : "";

  return (
    <div
      className={cn(
        "bg-card min-w-[150px] rounded-lg border-2 px-3 py-2 shadow-sm",
        ACCENT[t] ?? "border-border",
        selected && "ring-ring ring-2",
        invalid && "border-destructive",
      )}
    >
      {showTarget && (
        <Handle type="target" position={Position.Left} className="!bg-muted-foreground" />
      )}
      <div className="text-[13px] font-medium">{NODE_LABEL[t] ?? t}</div>
      {subtitle && <div className="text-muted-foreground text-[11px]">{subtitle}</div>}
      {showSource && (
        <Handle type="source" position={Position.Right} className="!bg-muted-foreground" />
      )}
    </div>
  );
}
