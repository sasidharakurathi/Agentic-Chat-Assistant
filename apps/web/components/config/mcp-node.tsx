"use client";

import { useId } from "react";

import { Field } from "@/components/config/panels";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Lamp, type LampTone } from "@/components/ui/lamp";
import { SectionHeading } from "@/components/ui/section-heading";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { McpServer } from "@/lib/api";
import {
  MODE_TEXT,
  OUTCOME_TEXT,
  asMode,
  effectiveMode,
  outcome,
  type Mode,
  type Outcome,
} from "@/lib/mcp-rules";
import { cn } from "@/lib/utils";

type Data = Record<string, unknown>;

const TRANSPORT_LABEL: Record<string, string> = {
  stdio: "Local command",
  http: "Remote (HTTP)",
  sse: "Remote (SSE)",
};

const OUTCOME_TONE: Record<Outcome, LampTone> = {
  runs: "success",
  asks: "neutral",
  never: "destructive",
};

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** The server's state as a status plate: lamp and word, never a raw enum. */
function ServerStatus({ server }: { server: McpServer }) {
  if (!server.enabled) return <Badge variant="muted">Switched off</Badge>;
  if (server.status === "ok") return <Badge variant="success">Connected</Badge>;
  if (server.status === "error") return <Badge variant="destructive">Failed</Badge>;
  return (
    <Badge variant="muted" className="text-foreground">
      <Lamp tone="off" />
      Not checked
    </Badge>
  );
}

/** The drawer of an `mcp_server` canvas node: which of the server's tools
 *  this assistant version may use, and how each is approved.
 *
 *  Everything here is per *version*: it lives on the node, compiles into the
 *  config, and publishing freezes it. What the server offers comes from its
 *  last discovery (the MCP servers tab); nothing is allowed until chosen
 *  here. */
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
  const ids = useId();
  const allow = new Set((data.tool_allowlist as string[] | undefined) ?? []);
  const rules = (data.tool_approvals as Record<string, string> | undefined) ?? {};
  const serverRule = asMode(data.approval);

  if (!server) {
    return (
      <Alert>
        This MCP server was removed from the assistant. Remove this node, or add the server again in
        the MCP servers tab.
      </Alert>
    );
  }

  const offered = server.tools;
  const offeredNames = new Set(offered.map((t) => t.name));
  const stale = [...allow].filter((name) => !offeredNames.has(name)).sort();
  const allowedCount = [...allow].filter((t) => offeredNames.has(t)).length;

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
    <div className="flex flex-col gap-6 text-sm">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-h4 min-w-0 font-semibold break-words">{server.name}</span>
          <ServerStatus server={server} />
          <Badge variant="muted">{TRANSPORT_LABEL[server.transport] ?? server.transport}</Badge>
        </div>
        {!server.enabled && (
          <p className="text-muted-foreground text-small max-w-[60ch]">
            Switched off in the MCP servers tab. None of its tools are offered until it is back on.
          </p>
        )}
      </div>

      {assistantDefault === "deny" && (
        <Alert tone="warning">
          MCP tools are switched off for this whole assistant, under Approvals in the Settings tab,
          so none of these will run.
        </Alert>
      )}

      <Field
        label="Approval rule for this server"
        hint="A tool's own rule, below, wins over this one."
      >
        <Select
          value={serverRule ?? ""}
          onChange={(e) => onChange({ approval: asMode(e.target.value) })}
        >
          <option value="">Same as the assistant ({MODE_TEXT[assistantDefault]})</option>
          {(["require", "auto", "deny"] as const).map((m) => (
            <option key={m} value={m}>
              {MODE_TEXT[m]}
            </option>
          ))}
        </Select>
      </Field>

      <section aria-labelledby={`${ids}-tools`} className="flex flex-col gap-3">
        <SectionHeading
          level={4}
          id={`${ids}-tools`}
          title="Tools this assistant may use"
          description={
            offered.length > 0 ? (
              <span className="num">
                {allowedCount} of {offered.length} allowed
              </span>
            ) : undefined
          }
          actions={
            offered.length > 0 ? (
              <>
                <Button
                  type="button"
                  variant="link"
                  size="sm"
                  onClick={() => setAllow(new Set([...offeredNames, ...stale]))}
                >
                  Allow all
                </Button>
                <Button type="button" variant="link" size="sm" onClick={() => setAllow(new Set())}>
                  Allow none
                </Button>
              </>
            ) : undefined
          }
        />
        {offered.length === 0 && (
          <p className="text-muted-foreground max-w-[60ch]">
            No tools found yet. Press Discover tools on this server in the MCP servers tab.
          </p>
        )}
        {offered.length > 0 && (
          <ul className="border-border divide-border flex flex-col divide-y border-y">
            {offered.map((tool) => {
              const on = allow.has(tool.name);
              const toolRule = asMode(rules[tool.name]);
              const result = outcome(
                effectiveMode(assistantDefault, serverRule, toolRule),
                tool.read_only,
              );
              const rowId = `${ids}-${tool.name}`;
              return (
                <li key={tool.name} className="flex flex-col gap-2 py-3">
                  <label
                    htmlFor={`${rowId}-switch`}
                    className="flex cursor-pointer items-start justify-between gap-4"
                  >
                    <span className="flex min-w-0 flex-col gap-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span
                          className={cn("font-medium break-all", !on && "text-muted-foreground")}
                        >
                          {tool.name}
                        </span>
                        {tool.read_only === true ? (
                          <Badge variant="success">Read-only</Badge>
                        ) : (
                          <Badge variant="warning">May change things</Badge>
                        )}
                      </span>
                      {tool.description && (
                        <span
                          id={`${rowId}-desc`}
                          className="text-muted-foreground text-small line-clamp-3"
                        >
                          {tool.description}
                        </span>
                      )}
                    </span>
                    <Switch
                      id={`${rowId}-switch`}
                      checked={on}
                      onCheckedChange={(v) => toggle(tool.name, v)}
                      aria-label={`Allow ${tool.name}`}
                      aria-describedby={tool.description ? `${rowId}-desc` : undefined}
                      className="mt-0.5"
                    />
                  </label>
                  {on && (
                    <div className="border-border ml-1 flex flex-col gap-1.5 border-l-2 pl-4">
                      <Select
                        aria-label={`Rule for ${tool.name}`}
                        aria-describedby={`${rowId}-outcome`}
                        value={toolRule ?? ""}
                        onChange={(e) => setRule(tool.name, asMode(e.target.value))}
                      >
                        <option value="">Same as the server</option>
                        <option value="require">{MODE_TEXT.require}</option>
                        {/* `auto` only skips the person for tools the server
                            declares read-only; offering it elsewhere would
                            promise something the server will not do. */}
                        {tool.read_only === true && (
                          <option value="auto">Run without asking</option>
                        )}
                        <option value="deny">{MODE_TEXT.deny}</option>
                      </Select>
                      <span
                        id={`${rowId}-outcome`}
                        className="text-small flex items-center gap-1.5"
                      >
                        <Lamp tone={OUTCOME_TONE[result]} />
                        {OUTCOME_TEXT[result]}
                      </span>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {stale.length > 0 && (
          <Alert tone="warning">
            <p>
              {stale.length} allowed {plural(stale.length, "tool is", "tools are")} no longer
              offered by the server: {stale.join(", ")}. The model doesn&apos;t see{" "}
              {plural(stale.length, "it", "them")}.
            </p>
            <Button
              type="button"
              variant="link"
              size="sm"
              className="mt-1"
              onClick={() => setAllow(new Set([...allow].filter((t) => offeredNames.has(t))))}
            >
              Remove {plural(stale.length, "it", "them")}
            </Button>
          </Alert>
        )}
      </section>
    </div>
  );
}
