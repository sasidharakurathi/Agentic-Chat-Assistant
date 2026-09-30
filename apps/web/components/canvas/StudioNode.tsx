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
  data_source: "border-node-datasource",
  database: "border-node-database",
  tool: "border-node-tool",
  mcp_server: "border-node-mcp",
  subagent: "border-node-subagent",
  router: "border-node-router",
};

export function StudioNode({ data, selected }: NodeProps) {
  const node = (data as { node: { type: string; data: Record<string, unknown> } }).node;
  const issues = (data as { issues?: { errors: string[]; warnings: string[] } }).issues;
  const errors = issues?.errors ?? [];
  const warnings = issues?.warnings ?? [];
  // The messages themselves, on hover: the node used to show only a red
  // border, with no hint of what was wrong.
  const tooltip = [...errors, ...warnings].join(" · ") || undefined;
  // Resolved by the build page from the assistant's real data sources — the
  // node itself only stores an id, and "Data source / 8f3a-…" tells nobody
  // which document they wired in.
  const label = (data as { sourceLabel?: string }).sourceLabel;
  // A run shown on the canvas (task 5.9): the nodes it touched stand out.
  const trace = (data as { trace?: "lit" | "dim" }).trace;
  const t = node.type;
  const showTarget = t !== "input";
  const showSource = t !== "output";

  const subtitle =
    t === "agent"
      ? String((node.data.models as { main?: { model?: string } })?.main?.model ?? "")
      : t === "tool"
        ? String(node.data.key ?? "")
        : t === "subagent"
          ? `${String(node.data.role ?? "")}${
              (node.data.model as { model?: string } | null)?.model
                ? ` · ${(node.data.model as { model: string }).model}`
                : ""
            }`
          : t === "router"
            ? `routes effort · ${String((node.data.model as { model?: string })?.model ?? "")}`
            : t === "data_source"
              ? (label ?? "unknown source")
              : t === "database"
                ? (label ?? "unknown connection")
                : t === "mcp_server"
                  ? `${label ?? "unknown server"} · ${
                      ((node.data.tool_allowlist as string[] | undefined) ?? []).length
                    } tools allowed`
                  : t === "knowledge_base"
                    ? `${(node.data.retrieval as { rerank_top_n?: number })?.rerank_top_n ?? "?"} results`
                    : "";

  return (
    <div
      title={tooltip}
      className={cn(
        "bg-card relative min-w-[150px] rounded-lg border-2 px-3 py-2 shadow-sm",
        ACCENT[t] ?? "border-border",
        selected && "ring-ring ring-2",
        trace === "lit" && "ring-primary shadow-md ring-2 ring-offset-2",
        trace === "dim" && "opacity-35",
        warnings.length > 0 && "border-warning",
        errors.length > 0 && "border-destructive",
      )}
    >
      {(errors.length > 0 || warnings.length > 0) && (
        <span
          aria-label={`${errors.length} error(s), ${warnings.length} warning(s)`}
          className={cn(
            "absolute -top-2 -right-2 flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[10px] font-semibold text-white",
            errors.length > 0 ? "bg-destructive" : "bg-warning",
          )}
        >
          {errors.length || warnings.length}
        </span>
      )}
      {showTarget && <Handle type="target" position={Position.Left} className="studio-handle" />}
      <div className="text-[13px] font-medium">{NODE_LABEL[t] ?? t}</div>
      {subtitle && <div className="text-muted-foreground text-[11px]">{subtitle}</div>}
      {showSource && <Handle type="source" position={Position.Right} className="studio-handle" />}
    </div>
  );
}
