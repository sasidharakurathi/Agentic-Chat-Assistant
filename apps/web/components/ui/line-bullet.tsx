import {
  Bot,
  Circle,
  Database,
  FileText,
  Library,
  LogIn,
  LogOut,
  NotebookText,
  Plug,
  ShieldCheck,
  Split,
  Users,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** Node types the pipeline knows about. The graph stores `type` as a
 *  string, so every helper here also accepts unknown strings. */
export type NodeKind =
  | "input"
  | "guardrail"
  | "router"
  | "agent"
  | "output"
  | "knowledge_base"
  | "data_source"
  | "memory"
  | "tool"
  | "database"
  | "mcp_server"
  | "subagent";

/** What kind of capability a line is (docs/DESIGN.md section 2): the main
 *  line, or one of three families joining the Agent. */
export type LineFamily = "main" | "knowledge" | "action" | "helper";

/** One source of truth for the glyph drawn in a node type's bullet. The
 *  Agent normally shows its stop number instead of `Bot`. */
export const NODE_GLYPH: Record<NodeKind, LucideIcon> = {
  input: LogIn,
  guardrail: ShieldCheck,
  router: Split,
  agent: Bot,
  output: LogOut,
  knowledge_base: Library,
  data_source: FileText,
  memory: NotebookText,
  tool: Wrench,
  database: Database,
  mcp_server: Plug,
  subagent: Users,
};

/** Plain name of each node type, used as the bullet's accessible name. */
export const NODE_TYPE_LABEL: Record<NodeKind, string> = {
  input: "Input",
  guardrail: "Guardrails",
  router: "Router",
  agent: "Agent",
  output: "Output",
  knowledge_base: "Knowledge base",
  data_source: "Data source",
  memory: "Memory",
  tool: "Tool",
  database: "Database",
  mcp_server: "MCP server",
  subagent: "Subagent",
};

/** Plural names, for counts like "Data sources, 5" in the canvas Key. */
export const NODE_TYPE_PLURAL: Record<NodeKind, string> = {
  input: "Input",
  guardrail: "Guardrails",
  router: "Routers",
  agent: "Agents",
  output: "Output",
  knowledge_base: "Knowledge bases",
  data_source: "Data sources",
  memory: "Memory",
  tool: "Tools",
  database: "Databases",
  mcp_server: "MCP servers",
  subagent: "Subagents",
};

export const NODE_FAMILY: Record<NodeKind, LineFamily> = {
  input: "main",
  guardrail: "main",
  router: "main",
  agent: "main",
  output: "main",
  knowledge_base: "knowledge",
  data_source: "knowledge",
  memory: "knowledge",
  tool: "action",
  database: "action",
  mcp_server: "action",
  subagent: "helper",
};

export const FAMILY_LABEL: Record<LineFamily, string> = {
  main: "Pipeline",
  knowledge: "Knowledge",
  action: "Actions",
  helper: "Helpers",
};

/** Line colour classes per node type. Written out in full so Tailwind sees
 *  them; use these rather than building `bg-node-${x}` strings. */
export const NODE_BG: Record<NodeKind, string> = {
  input: "bg-node-main",
  guardrail: "bg-node-guardrail",
  router: "bg-node-router",
  agent: "bg-node-agent",
  output: "bg-node-main",
  knowledge_base: "bg-node-kb",
  data_source: "bg-node-datasource",
  memory: "bg-node-memory",
  tool: "bg-node-tool",
  database: "bg-node-database",
  mcp_server: "bg-node-mcp",
  subagent: "bg-node-subagent",
};
export const NODE_BORDER: Record<NodeKind, string> = {
  input: "border-node-main",
  guardrail: "border-node-guardrail",
  router: "border-node-router",
  agent: "border-node-agent",
  output: "border-node-main",
  knowledge_base: "border-node-kb",
  data_source: "border-node-datasource",
  memory: "border-node-memory",
  tool: "border-node-tool",
  database: "border-node-database",
  mcp_server: "border-node-mcp",
  subagent: "border-node-subagent",
};
export const NODE_TEXT: Record<NodeKind, string> = {
  input: "text-node-main",
  guardrail: "text-node-guardrail",
  router: "text-node-router",
  agent: "text-node-agent",
  output: "text-node-main",
  knowledge_base: "text-node-kb",
  data_source: "text-node-datasource",
  memory: "text-node-memory",
  tool: "text-node-tool",
  database: "text-node-database",
  mcp_server: "text-node-mcp",
  subagent: "text-node-subagent",
};
/** CSS colour value (a token) per node type, for SVG strokes and edges. */
export const NODE_COLOR_VAR: Record<NodeKind, string> = {
  input: "var(--node-main)",
  guardrail: "var(--node-guardrail)",
  router: "var(--node-router)",
  agent: "var(--node-agent)",
  output: "var(--node-main)",
  knowledge_base: "var(--node-kb)",
  data_source: "var(--node-datasource)",
  memory: "var(--node-memory)",
  tool: "var(--node-tool)",
  database: "var(--node-database)",
  mcp_server: "var(--node-mcp)",
  subagent: "var(--node-subagent)",
};

export function isNodeKind(type: string): type is NodeKind {
  return Object.prototype.hasOwnProperty.call(NODE_FAMILY, type);
}

/** The line family of a node type; unknown types count as the main line. */
export function nodeFamily(type: string): LineFamily {
  return isNodeKind(type) ? NODE_FAMILY[type] : "main";
}

/** The plain name of a node type ("MCP server"); unknown types as given. */
export function nodeTypeLabel(type: string): string {
  return isNodeKind(type) ? NODE_TYPE_LABEL[type] : type;
}

/** Stations on the main line, in route order. */
export const MAIN_LINE_ORDER: readonly NodeKind[] = [
  "input",
  "guardrail",
  "router",
  "agent",
  "output",
];

const SIZE = {
  16: { box: "size-4", glyph: "size-2.5", num: "text-small", ring: "border-2" },
  20: { box: "size-5", glyph: "size-3", num: "text-small", ring: "border-2" },
  24: { box: "size-6", glyph: "size-3.5", num: "text-label", ring: "border-2" },
} as const;

/** A line bullet (docs/DESIGN.md section 5): a circle in the node type's
 *  line colour holding its lucide glyph, or the stop number for main-line
 *  stations. Hollow (a 2px ring, no fill) means switched off; `unlit` greys
 *  it out for a traced run or the Key. Its accessible name is the type
 *  label, unless `label` overrides it or `decorative` hides it (when the
 *  same name is already written next to it). */
export function LineBullet({
  type,
  size = 20,
  number,
  hollow = false,
  unlit = false,
  label,
  decorative = false,
  className,
  ...props
}: Omit<HTMLAttributes<HTMLSpanElement>, "children"> & {
  type: string;
  size?: 16 | 20 | 24;
  /** Stop number for a main-line station. */
  number?: number;
  hollow?: boolean;
  unlit?: boolean;
  label?: string;
  decorative?: boolean;
}) {
  const kind = isNodeKind(type) ? type : null;
  const Glyph = kind ? NODE_GLYPH[kind] : Circle;
  const s = SIZE[size];
  const name = label ?? nodeTypeLabel(type);

  const colour = unlit
    ? hollow
      ? "border-line-unlit text-line-unlit"
      : "bg-line-unlit text-muted-foreground"
    : hollow
      ? cn(kind ? NODE_BORDER[kind] : "border-node-main", kind ? NODE_TEXT[kind] : "text-node-main")
      : cn(kind ? NODE_BG[kind] : "bg-node-main", "text-node-icon");

  return (
    <span
      role={decorative ? undefined : "img"}
      aria-label={decorative ? undefined : name}
      aria-hidden={decorative ? true : undefined}
      title={decorative ? undefined : name}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full",
        s.box,
        hollow && cn(s.ring, "bg-transparent"),
        colour,
        className,
      )}
      {...props}
    >
      {number !== undefined ? (
        <span aria-hidden className={cn("font-condensed num leading-none font-semibold", s.num)}>
          {number}
        </span>
      ) : (
        <Glyph aria-hidden className={s.glyph} strokeWidth={2.25} />
      )}
    </span>
  );
}
