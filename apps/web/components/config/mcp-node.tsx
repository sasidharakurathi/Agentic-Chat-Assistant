"use client";

import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import type { McpServer } from "@/lib/api";
import {
  MODE_TEXT,
  OUTCOME_TEXT,
  asMode,
  effectiveMode,
  outcome,
  type Mode,
} from "@/lib/mcp-rules";
import { cn } from "@/lib/utils";

type Data = Record<string, unknown>;

/** The drawer of an `mcp_server` canvas node (task 4.10): which of the
 *  server's tools this assistant version may use, and how each is approved.
 *
 *  Everything here is per *version*: it lives on the node, compiles into the
 *  config, and publishing freezes it. What the server offers comes from its
 *  last discovery (the MCP tab); nothing is allowed until chosen here. */
export function McpServerNodePanel({
  data,
  server,
  assistantDefault,
  onChange,
}: {
  data: Data;
  server: McpServer | undefined;
  /** The assistant's `approval_policy.mcp_default`. */
  assistantDefault: Mode;
  onChange: (patch: Data) => void;
}) {
  const allow = new Set((data.tool_allowlist as string[] | undefined) ?? []);
  const rules = (data.tool_approvals as Record<string, string> | undefined) ?? {};
  const serverRule = asMode(data.approval);

  if (!server) {
    return (
      <p className="text-destructive text-sm">
        This MCP server is not registered on this assistant any more. Remove the node, or add the
        server again in the <strong>MCP</strong> tab.
      </p>
    );
  }

  const offered = server.tools;
  const offeredNames = new Set(offered.map((t) => t.name));
  const stale = [...allow].filter((name) => !offeredNames.has(name)).sort();

  const setAllow = (next: Set<string>) => {
    // A rule for a tool that is no longer allowed would sit there unseen.
    const keptRules = Object.fromEntries(Object.entries(rules).filter(([t]) => next.has(t)));
    onChange({ tool_allowlist: [...next].sort(), tool_approvals: keptRules });
  };
  const toggle = (name: string, on: boolean) => {
    const next = new Set(allow);
    if (on) next.add(name);
    else next.delete(name);
    setAllow(next);
  };
  const setRule = (name: string, mode: Mode | null) => {
    const next = { ...rules };
    if (mode) next[name] = mode;
    else delete next[name];
    onChange({ tool_approvals: next });
  };

  return (
    <div className="flex flex-col gap-4 text-sm">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{server.name}</span>
          <Badge variant="muted">{server.transport}</Badge>
          {!server.enabled && <Badge variant="warning">off</Badge>}
          {server.status === "error" && <Badge variant="destructive">error</Badge>}
        </div>
        {!server.enabled && (
          <p className="text-muted-foreground mt-1 text-xs">
            Switched off in the MCP tab: none of its tools are offered until it is on.
          </p>
        )}
      </div>

      {assistantDefault === "deny" && (
        <p className="border-warning bg-warning/10 rounded-md border px-3 py-2 text-xs">
          MCP tools are switched off for this whole assistant (Panels › Approvals › MCP server
          tools), so none of these will run.
        </p>
      )}

      <label className="flex flex-col gap-1.5">
        <span className="text-xs font-medium">This server&apos;s rule</span>
        <Select
          value={serverRule ?? ""}
          onChange={(e) => onChange({ approval: asMode(e.target.value) })}
        >
          <option value="">Use the assistant default ({MODE_TEXT[assistantDefault]})</option>
          {(["require", "auto", "deny"] as const).map((m) => (
            <option key={m} value={m}>
              {MODE_TEXT[m]}
            </option>
          ))}
        </Select>
        <span className="text-muted-foreground text-xs">
          A tool&apos;s own rule, below, wins over this one.
        </span>
      </label>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium">
            Tools this assistant may use ({[...allow].filter((t) => offeredNames.has(t)).length} of{" "}
            {offered.length})
          </span>
          {offered.length > 0 && (
            <span className="flex gap-2 text-xs">
              <button
                type="button"
                className="text-primary hover:underline"
                onClick={() => setAllow(new Set([...offeredNames, ...stale]))}
              >
                All
              </button>
              <button
                type="button"
                className="text-primary hover:underline"
                onClick={() => setAllow(new Set())}
              >
                None
              </button>
            </span>
          )}
        </div>
        {offered.length === 0 && (
          <p className="text-muted-foreground text-xs">
            No tools discovered yet. Press <strong>Discover tools</strong> on this server in the{" "}
            <strong>MCP</strong> tab.
          </p>
        )}
        <ul className="flex flex-col gap-2">
          {offered.map((tool) => {
            const on = allow.has(tool.name);
            const toolRule = asMode(rules[tool.name]);
            const result = outcome(
              effectiveMode(assistantDefault, serverRule, toolRule),
              tool.read_only,
            );
            return (
              <li
                key={tool.name}
                className={cn("border-border rounded-md border px-2.5 py-2", !on && "opacity-70")}
              >
                <label className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={on}
                    onChange={(e) => toggle(tool.name, e.target.checked)}
                    aria-label={`Allow ${tool.name}`}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-1.5">
                      <code className="text-xs font-medium">{tool.name}</code>
                      {tool.read_only === true ? (
                        <Badge variant="success">read-only</Badge>
                      ) : (
                        <Badge variant="muted">may change things</Badge>
                      )}
                    </span>
                    {tool.description && (
                      <span className="text-muted-foreground mt-0.5 line-clamp-3 block text-xs">
                        {tool.description}
                      </span>
                    )}
                  </span>
                </label>
                {on && (
                  <div className="mt-2 flex flex-col gap-1 pl-6">
                    <Select
                      aria-label={`Rule for ${tool.name}`}
                      value={toolRule ?? ""}
                      onChange={(e) => setRule(tool.name, asMode(e.target.value))}
                      className="h-8 text-xs"
                    >
                      <option value="">Use the server&apos;s rule</option>
                      <option value="require">{MODE_TEXT.require}</option>
                      {/* `auto` only skips the person for tools the server
                          declares read-only; offering it elsewhere would
                          promise something the server will not do. */}
                      {tool.read_only === true && <option value="auto">Run without asking</option>}
                      <option value="deny">{MODE_TEXT.deny}</option>
                    </Select>
                    <span
                      className={cn(
                        "text-xs",
                        result === "runs" && "text-success",
                        result === "never" && "text-destructive",
                        result === "asks" && "text-muted-foreground",
                      )}
                    >
                      {OUTCOME_TEXT[result]}
                    </span>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        {stale.length > 0 && (
          <div className="border-warning bg-warning/10 rounded-md border px-3 py-2 text-xs">
            Allowed, but no longer offered by the server: <code>{stale.join(", ")}</code>. They are
            not offered to the model.{" "}
            <button
              type="button"
              className="text-primary hover:underline"
              onClick={() => setAllow(new Set([...allow].filter((t) => offeredNames.has(t))))}
            >
              Remove them
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
