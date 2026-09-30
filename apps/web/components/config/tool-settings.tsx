"use client";

import type { ReactNode } from "react";

import { Field, Toggle } from "@/components/config/panels";
import { Alert } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import type { AssistantConfig } from "@/lib/api";
import { useOfflineMode } from "@/lib/instance";

/** Per-tool settings: the same editors in the Settings tab and in a canvas
 *  tool node's drawer, so the two can never disagree about what a setting
 *  means. */

type Data = Record<string, unknown>;
export type ApprovalMode = "auto" | "require" | "deny";

const APPROVAL_LABEL: Record<ApprovalMode, string> = {
  require: "Ask a person first",
  auto: "Run read-only tools without asking",
  deny: "Never allow",
};

/** Actions that change something always ask a person unless forbidden: the
 *  server lets `auto` skip the human for low-risk calls only, and a write is
 *  never low risk. Offering "run without asking" for them would promise
 *  something the server will not do. */
const ASK_OR_FORBID = ["require", "deny"] as const;
/** For MCP tools, `auto` is real, but only for tools the server declares
 *  read-only. */
const MCP_MODES = ["require", "auto", "deny"] as const;
const STRICTNESS: Record<ApprovalMode, number> = { auto: 0, require: 1, deny: 2 };

/** Mirrors `approvals.stricter` on the server. */
export function stricter(a: ApprovalMode, b: ApprovalMode): ApprovalMode {
  return STRICTNESS[a] >= STRICTNESS[b] ? a : b;
}

const mode = (v: unknown, d: ApprovalMode = "require"): ApprovalMode =>
  v === "auto" || v === "require" || v === "deny" ? v : d;

/** `https://Example.com/x, *.docs.org` -> `["example.com", "docs.org"]`,
 *  the same normalization the server applies. */
export function parseDomains(raw: string): string[] {
  const out: string[] = [];
  for (const part of raw.split(/[\s,]+/)) {
    let d = part.trim().toLowerCase();
    if (!d) continue;
    if (d.includes("://")) d = d.split("://")[1];
    d = d
      .split("/")[0]
      .split(":")[0]
      .replace(/^\*\./, "")
      .replace(/^\.+|\.+$/g, "");
    if (d && !out.includes(d)) out.push(d);
  }
  return out;
}

function DomainsField({
  value,
  onCommit,
  hint,
}: {
  value: string[];
  onCommit: (domains: string[]) => void;
  hint: string;
}) {
  const text = value.join(", ");
  return (
    <Field label="Allowed sites" hint={hint}>
      <Input
        // Re-mount when the saved value changes, so the box shows what the
        // server normalized it to.
        key={text}
        defaultValue={text}
        placeholder="Any public site"
        onBlur={(e) => {
          const next = parseDomains(e.target.value);
          if (next.join(",") !== value.join(",")) onCommit(next);
        }}
      />
    </Field>
  );
}

function ApprovalSelect({
  label,
  value,
  onChange,
  hint,
  modes = ASK_OR_FORBID,
}: {
  label: string;
  value: ApprovalMode;
  onChange: (m: ApprovalMode) => void;
  hint?: string;
  modes?: readonly ApprovalMode[];
}) {
  // A stored `auto` where it cannot apply behaves as "ask", so show that.
  const shown = modes.includes(value) ? value : "require";
  return (
    <Field label={label} hint={hint}>
      <Select value={shown} onChange={(e) => onChange(mode(e.target.value))}>
        {modes.map((m) => (
          <option key={m} value={m}>
            {APPROVAL_LABEL[m]}
          </option>
        ))}
      </Select>
    </Field>
  );
}

/** Settings that only apply while the tool's toggle is on. */
function Nested({ children }: { children: ReactNode }) {
  return <div className="border-border mb-2 ml-1 border-l-2 pl-4">{children}</div>;
}

// ── the two tools with settings ──────────────────────────────

export function WebSearchSettings({
  maxUses,
  domains,
  onChange,
}: {
  maxUses: number;
  domains: string[];
  onChange: (patch: { max_uses?: number; allowed_domains?: string[] }) => void;
}) {
  const offline = useOfflineMode();
  return (
    <div className="flex flex-col gap-4">
      {offline && (
        <Alert tone="warning">
          This server runs offline, so web search is off for every assistant here. These settings
          are kept and apply again once it is back online.
        </Alert>
      )}
      <Field label="Searches per message" hint="1 to 50. Any more in the same message are refused.">
        <Input
          key={maxUses}
          type="number"
          inputMode="numeric"
          className="num"
          min={1}
          max={50}
          defaultValue={String(maxUses)}
          onBlur={(e) => {
            const n = Math.min(50, Math.max(1, Math.round(Number(e.target.value) || maxUses)));
            if (n !== maxUses) onChange({ max_uses: n });
          }}
        />
      </Field>
      <DomainsField
        value={domains}
        onCommit={(d) => onChange({ allowed_domains: d })}
        hint="Separate sites with commas. Searches only return results from these sites and their subdomains."
      />
    </div>
  );
}

export function HttpRequestSettings({
  domains,
  approval,
  policy,
  onChange,
}: {
  domains: string[];
  approval: ApprovalMode;
  /** The assistant-wide rule for non-GET requests. */
  policy: ApprovalMode;
  onChange: (patch: { allowed_domains?: string[]; approval?: ApprovalMode }) => void;
}) {
  const effective = stricter(approval, policy);
  return (
    <div className="flex flex-col gap-4">
      <DomainsField
        value={domains}
        onCommit={(d) => onChange({ allowed_domains: d })}
        hint="Separate sites with commas. Leave empty to allow any public site. Private and internal addresses are always blocked."
      />
      <ApprovalSelect
        label="Requests that change something"
        value={approval}
        onChange={(m) => onChange({ approval: m })}
        hint="POST, PUT, PATCH and DELETE. GET and HEAD requests only read, and always run."
      />
      {effective !== approval && (
        <Alert tone="warning">
          The assistant&apos;s approval rules are stricter, so these requests will{" "}
          {effective === "deny" ? "never be allowed" : "still ask a person"}. Change that under
          Approvals in the Settings tab.
        </Alert>
      )}
    </div>
  );
}

// ── Settings tab ─────────────────────────────────────────────

export const BUILTIN_TOOLS = ["calculator", "datetime", "web_search", "http_request"] as const;

const TOOL_LABEL: Record<string, string> = {
  calculator: "Calculator",
  datetime: "Date and time",
  web_search: "Web search",
  http_request: "HTTP requests",
};

const TOOL_HINT: Record<(typeof BUILTIN_TOOLS)[number], string> = {
  calculator: "Does exact arithmetic instead of estimating.",
  datetime: "Knows today's date and the time in any time zone.",
  web_search: "Searches the web and cites what it finds.",
  http_request: "Calls web APIs on the sites you allow.",
};

export function ToolsPanel({
  config,
  onChange,
}: {
  config: AssistantConfig;
  onChange: (next: AssistantConfig) => void;
}) {
  const tools = config.tools as unknown as Record<string, Data>;
  const policy = mode((config.approval_policy as unknown as Data | undefined)?.http_non_get);
  const patch = (key: string, p: Data) =>
    onChange({
      ...config,
      tools: { ...config.tools, [key]: { ...tools[key], ...p } },
    } as AssistantConfig);

  const web = tools.web_search ?? {};
  const http = tools.http_request ?? {};
  return (
    <div className="flex flex-col gap-3">
      {BUILTIN_TOOLS.map((key) => (
        <div key={key} className="flex flex-col gap-3">
          <Toggle
            label={TOOL_LABEL[key]}
            checked={tools[key]?.enabled === true}
            onChange={(v) => patch(key, { enabled: v })}
            hint={TOOL_HINT[key]}
          />
          {key === "web_search" && web.enabled === true && (
            <Nested>
              <WebSearchSettings
                maxUses={Number(web.max_uses ?? 5)}
                domains={(web.allowed_domains as string[] | undefined) ?? []}
                onChange={(p) => patch("web_search", p)}
              />
            </Nested>
          )}
          {key === "http_request" && http.enabled === true && (
            <Nested>
              <HttpRequestSettings
                domains={(http.allowed_domains as string[] | undefined) ?? []}
                approval={mode(http.approval)}
                policy={policy}
                onChange={(p) => patch("http_request", p)}
              />
            </Nested>
          )}
        </div>
      ))}
    </div>
  );
}

const POLICY_FIELDS: {
  key: string;
  label: string;
  hint: string;
  modes?: readonly ApprovalMode[];
}[] = [
  {
    key: "db_write",
    label: "Database writes",
    hint: "INSERT, UPDATE and DELETE, on connections that allow writes.",
  },
  {
    key: "db_ddl",
    label: "Database schema changes",
    hint: "CREATE, ALTER and DROP, on connections that allow them.",
  },
  {
    key: "http_non_get",
    label: "HTTP requests that change something",
    hint: "The stricter of this and the HTTP requests tool's own setting applies.",
  },
  {
    key: "mcp_default",
    label: "MCP server tools",
    hint: "Tools a server doesn't mark as read-only always ask first, whatever you choose here.",
    modes: MCP_MODES,
  },
];

export function ApprovalPolicyPanel({
  config,
  onChange,
}: {
  config: AssistantConfig;
  onChange: (next: AssistantConfig) => void;
}) {
  const policy = (config.approval_policy ?? {}) as unknown as Data;
  return (
    <div className="flex flex-col gap-4">
      {POLICY_FIELDS.map((f) => (
        <ApprovalSelect
          key={f.key}
          label={f.label}
          hint={f.hint}
          modes={f.modes}
          value={mode(policy[f.key])}
          onChange={(m) =>
            onChange({
              ...config,
              approval_policy: { ...policy, [f.key]: m },
            } as AssistantConfig)
          }
        />
      ))}
    </div>
  );
}

// ── canvas drawer ────────────────────────────────────────────

export function ToolNodePanel({
  data,
  policy,
  onChange,
}: {
  data: Data;
  policy: ApprovalMode;
  onChange: (patch: Data) => void;
}) {
  const key = String(data.key ?? "");
  const cfg = (data.config ?? {}) as Data;
  const setCfg = (p: Data) => onChange({ config: { ...cfg, ...p } });

  if (key === "web_search") {
    return (
      <WebSearchSettings
        maxUses={Number(cfg.max_uses ?? 5)}
        domains={(cfg.allowed_domains as string[] | undefined) ?? []}
        onChange={setCfg}
      />
    );
  }
  if (key === "http_request") {
    return (
      <HttpRequestSettings
        domains={(cfg.allowed_domains as string[] | undefined) ?? []}
        approval={mode(data.approval)}
        policy={policy}
        onChange={({ approval, ...rest }) => {
          if (approval) onChange({ approval });
          if (Object.keys(rest).length) setCfg(rest);
        }}
      />
    );
  }
  return (
    <p className="text-muted-foreground max-w-[60ch] text-sm">
      {TOOL_LABEL[key] ?? key} only reads, so there is nothing to set. Remove the node to switch it
      off.
    </p>
  );
}

export { TOOL_LABEL };
