"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  mcpServers,
  type McpCheckResult,
  type McpPreset,
  type McpRunnerStatus,
  type McpServer,
  type McpTool,
} from "@/lib/api";
import {
  describeLimits,
  missingSecrets,
  nameProblem,
  parseArgs,
  parsePairs,
  type Limits,
} from "@/lib/mcp-forms";

type Transport = "stdio" | "http" | "sse";

const TRANSPORTS: { value: Transport; label: string; hint: string }[] = [
  {
    value: "http",
    label: "HTTP (streamable)",
    hint: "A remote server at an https:// URL. The current standard transport.",
  },
  {
    value: "sse",
    label: "SSE (legacy)",
    hint: "A remote server using the older server-sent events transport.",
  },
  {
    value: "stdio",
    label: "Local command (stdio)",
    hint: "A program this platform starts itself, such as an npx or uvx package.",
  },
];

function message(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : "Something went wrong";
}

/** The MCP servers tab: register, edit and remove servers (task 4.3), and
 *  see and set how local-command servers are contained (4.4), and check a
 *  server and see the tools it offers (4.5). Choosing which of them an
 *  assistant uses happens on its canvas node, so it is versioned. */
export function McpServersManager({ assistantId }: { assistantId: string }) {
  const [rows, setRows] = useState<McpServer[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [runner, setRunner] = useState<McpRunnerStatus | null>(null);
  const [presets, setPresets] = useState<McpPreset[]>([]);
  const [preset, setPreset] = useState<McpPreset | null>(null);

  useEffect(() => {
    mcpServers
      .presets()
      .then(setPresets)
      .catch(() => setPresets([]));
  }, []);

  useEffect(() => {
    mcpServers
      .runner()
      .then(setRunner)
      .catch(() => setRunner(null));
  }, []);

  const load = useCallback(async () => {
    try {
      setRows(await mcpServers.list(assistantId));
    } catch (err) {
      setError(message(err));
    }
  }, [assistantId]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = useCallback(
    async (fn: () => Promise<unknown>): Promise<boolean> => {
      setBusy(true);
      setError(null);
      try {
        await fn();
        await load();
        return true;
      } catch (err) {
        setError(message(err));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 overflow-auto p-6">
      <section className="border-border rounded-lg border">
        <div className="border-border flex items-center justify-between border-b px-4 py-3">
          <h2 className="text-sm font-semibold">MCP servers</h2>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setPreset(null);
              setAdding((v) => !v);
            }}
          >
            {adding ? "Cancel" : "Add server"}
          </Button>
        </div>
        {adding ? (
          <ServerForm
            key={preset?.key ?? "blank"}
            preset={preset}
            busy={busy}
            onSubmit={(body) =>
              run(async () => {
                await mcpServers.create(assistantId, body);
                setAdding(false);
              })
            }
          />
        ) : (
          <p className="text-muted-foreground px-4 py-3 text-xs">
            An MCP server gives the assistant extra tools, like a ticket system or a code host.
            Header and environment values are encrypted and never shown again; only their names are.
          </p>
        )}
        {presets.length > 0 && (
          <Catalog
            presets={presets}
            taken={new Set((rows ?? []).map((r) => r.name))}
            onPick={(p) => {
              setPreset(p);
              setAdding(true);
            }}
          />
        )}
      </section>

      {runner && <RunnerBanner status={runner} />}

      {error && (
        <p className="text-destructive text-sm" role="alert">
          {error}
        </p>
      )}

      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold">Servers{rows ? ` (${rows.length})` : ""}</h2>
        {rows === null && <p className="text-muted-foreground text-sm">Loading…</p>}
        {rows?.length === 0 && (
          <p className="text-muted-foreground border-border rounded-md border border-dashed px-4 py-8 text-center text-sm">
            No MCP servers yet. Add one above.
          </p>
        )}
        <ul className="flex flex-col gap-2">
          {(rows ?? []).map((row) => (
            <ServerRow key={row.id} assistantId={assistantId} row={row} busy={busy} run={run} />
          ))}
        </ul>
      </section>
    </div>
  );
}

// ── add ──────────────────────────────────────────────────────

function ServerForm({
  busy,
  onSubmit,
  preset = null,
}: {
  busy: boolean;
  onSubmit: (body: Record<string, unknown>) => Promise<boolean>;
  /** Fill the form from the catalog. Secrets get their names only. */
  preset?: McpPreset | null;
}) {
  const [transport, setTransport] = useState<Transport>(preset?.transport ?? "http");
  const [name, setName] = useState(preset?.name ?? "");
  const [url, setUrl] = useState(preset?.url ?? "");
  const [command, setCommand] = useState(preset?.command ?? "");
  const [args, setArgs] = useState((preset?.args ?? []).join("\n"));
  const [pairs, setPairs] = useState(
    preset
      ? preset.transport === "stdio"
        ? preset.env.map((f) => `${f.name}=`).join("\n")
        : preset.headers.map((f) => `${f.name}: `).join("\n")
      : "",
  );
  const needed = preset ? (preset.transport === "stdio" ? preset.env : preset.headers) : [];
  const [problem, setProblem] = useState<string | null>(null);

  const stdio = transport === "stdio";
  const hint = TRANSPORTS.find((t) => t.value === transport)?.hint;

  const submit = () => {
    const bad = nameProblem(name.trim());
    if (bad) return setProblem(bad);
    const parsed = parsePairs(pairs, stdio ? "env" : "headers");
    if (parsed.error !== null) return setProblem(parsed.error);
    const missing = missingSecrets(
      needed.map((f) => f.name),
      parsed.value,
    );
    if (missing.length) return setProblem(`Fill in ${missing.join(", ")}.`);
    setProblem(null);
    void onSubmit(
      stdio
        ? {
            name: name.trim(),
            transport,
            command: command.trim(),
            args: parseArgs(args),
            env: parsed.value,
          }
        : { name: name.trim(), transport, url: url.trim(), headers: parsed.value },
    );
  };

  return (
    <form
      className="flex flex-col gap-3 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="mcp-name">Name</Label>
          <Input
            id="mcp-name"
            value={name}
            placeholder="github"
            onChange={(e) => setName(e.target.value.toLowerCase())}
          />
          <p className="text-muted-foreground text-xs">
            Lowercase letters, digits and hyphens. Tools appear as{" "}
            <code>mcp__{name || "name"}__tool</code>.
          </p>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="mcp-transport">Connection</Label>
          <Select
            id="mcp-transport"
            value={transport}
            onChange={(e) => setTransport(e.target.value as Transport)}
          >
            {TRANSPORTS.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </Select>
          {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
        </div>
      </div>

      {stdio ? (
        <>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mcp-command">Command</Label>
            <Input
              id="mcp-command"
              value={command}
              placeholder="npx"
              onChange={(e) => setCommand(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mcp-args">Arguments, one per line</Label>
            <Textarea
              id="mcp-args"
              rows={3}
              value={args}
              placeholder={"-y\n@modelcontextprotocol/server-github"}
              onChange={(e) => setArgs(e.target.value)}
            />
            <p className="text-muted-foreground text-xs">
              Shown in plain text. Put tokens and keys in the environment below instead.
            </p>
          </div>
        </>
      ) : (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="mcp-url">URL</Label>
          <Input
            id="mcp-url"
            value={url}
            placeholder="https://mcp.example.com/mcp"
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="mcp-pairs">
          {stdio ? "Environment variables, one per line" : "Headers, one per line"}
        </Label>
        <Textarea
          id="mcp-pairs"
          rows={3}
          value={pairs}
          className="font-mono text-xs"
          placeholder={stdio ? "GITHUB_TOKEN=ghp_…" : "Authorization: Bearer …"}
          onChange={(e) => setPairs(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
        <p className="text-muted-foreground text-xs">
          Encrypted when saved. You will see the names afterwards, never the values.
        </p>
      </div>

      {preset && (
        <div className="bg-muted/50 rounded-md px-3 py-2 text-xs">
          <p>
            From the catalog: <strong>{preset.title}</strong>.{" "}
            <a
              href={preset.docs_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary underline underline-offset-2"
            >
              Its documentation
            </a>
            . Review it before adding.
          </p>
          {needed.length > 0 && (
            <ul className="mt-1 ml-4 list-disc">
              {needed.map((f) => (
                <li key={f.name}>
                  <code>{f.name}</code>: {f.hint}
                </li>
              ))}
            </ul>
          )}
          {preset.needs_network && preset.transport === "stdio" && (
            <p className="text-muted-foreground mt-1">
              It downloads its package on first use, so the runner needs internet access (see the
              runner note below).
            </p>
          )}
        </div>
      )}
      {problem && <p className="text-destructive text-xs">{problem}</p>}
      <div className="flex justify-end">
        <Button type="submit" size="sm" disabled={busy}>
          Add server
        </Button>
      </div>
    </form>
  );
}

// ── one server ───────────────────────────────────────────────

function ServerRow({
  assistantId,
  row,
  busy,
  run,
}: {
  assistantId: string;
  row: McpServer;
  busy: boolean;
  run: (fn: () => Promise<unknown>) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [limitsOpen, setLimitsOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [result, setResult] = useState<{ kind: "check" | "discover"; r: McpCheckResult } | null>(
    null,
  );
  const [working, setWorking] = useState<"check" | "discover" | null>(null);
  const confirm = useConfirm();

  const act = (kind: "check" | "discover") =>
    void (async () => {
      setWorking(kind);
      setResult(null);
      try {
        await run(async () => {
          const r =
            kind === "check"
              ? await mcpServers.health(assistantId, row.id)
              : await mcpServers.discover(assistantId, row.id);
          setResult({ kind, r });
          if (kind === "discover" && r.ok) setToolsOpen(true);
        });
      } finally {
        setWorking(null);
      }
    })();
  const stdio = row.transport === "stdio";
  const secretNames = stdio ? row.env_keys : row.header_names;

  return (
    <li className="border-border rounded-md border px-4 py-3">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-medium">{row.name}</span>
            <Badge variant="muted">{row.transport}</Badge>
            <Badge
              variant={
                row.status === "ok" ? "success" : row.status === "error" ? "destructive" : "muted"
              }
            >
              {row.status === "unknown" ? "not checked" : row.status}
            </Badge>
            {!row.enabled && <Badge variant="warning">off</Badge>}
          </div>
          <p className="text-muted-foreground mt-1 font-mono text-xs break-all">
            {stdio ? [row.command, ...row.args].join(" ") : row.url}
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            {secretNames.length > 0
              ? `${stdio ? "Environment" : "Headers"}: ${secretNames.join(", ")} (values hidden)`
              : `No ${stdio ? "environment variables" : "headers"}`}
            {" · "}
            {row.tools.length > 0 ? (
              <button
                type="button"
                className="text-primary underline-offset-2 hover:underline"
                onClick={() => setToolsOpen((v) => !v)}
              >
                {row.tools.length} tool{row.tools.length === 1 ? "" : "s"}
                {toolsOpen ? " (hide)" : ""}
              </button>
            ) : (
              "tools not discovered yet"
            )}
            {row.last_checked_at && (
              <> · checked {new Date(row.last_checked_at).toLocaleString()}</>
            )}
          </p>
          {stdio && (
            <p className="text-muted-foreground mt-1 text-xs">
              Limits: {describeLimits(row.sandbox as Limits)}
            </p>
          )}
          {row.error && <p className="text-destructive mt-1 text-xs">{row.error}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <label className="text-muted-foreground flex items-center gap-2 text-xs">
            On
            <Switch
              checked={row.enabled}
              disabled={busy}
              onCheckedChange={(v) =>
                void run(() => mcpServers.update(assistantId, row.id, { enabled: v }))
              }
            />
          </label>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            title="Connect and complete the MCP handshake"
            onClick={() => act("check")}
          >
            {working === "check" ? "Checking…" : "Check"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            title="Connect and list the tools this server offers"
            onClick={() => act("discover")}
          >
            {working === "discover" ? "Discovering…" : "Discover tools"}
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => setEditing((v) => !v)}>
            {editing ? "Close" : "Edit"}
          </Button>
          {stdio && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => setLimitsOpen((v) => !v)}
            >
              Limits
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() =>
              void (async () => {
                const sure = await confirm({
                  title: `Remove "${row.name}"?`,
                  description:
                    "Its stored headers or environment are deleted too. Canvas nodes that use it will show an error until removed.",
                  confirmLabel: "Remove",
                  destructive: true,
                });
                if (sure) void run(() => mcpServers.remove(assistantId, row.id));
              })()
            }
          >
            Remove
          </Button>
        </div>
      </div>
      {result && <CheckOutcome kind={result.kind} result={result.r} />}
      {toolsOpen && row.tools.length > 0 && <ToolList tools={row.tools} />}
      {limitsOpen && (
        <LimitsForm
          limits={row.sandbox as Limits}
          busy={busy}
          onSave={async (sandbox) => {
            const ok = await run(() => mcpServers.update(assistantId, row.id, { sandbox }));
            if (ok) setLimitsOpen(false);
          }}
        />
      )}
      {editing && (
        <EditForm
          row={row}
          busy={busy}
          onSave={async (body) => {
            const ok = await run(() => mcpServers.update(assistantId, row.id, body));
            if (ok) setEditing(false);
          }}
        />
      )}
    </li>
  );
}

function EditForm({
  row,
  busy,
  onSave,
}: {
  row: McpServer;
  busy: boolean;
  onSave: (body: Record<string, unknown>) => Promise<void>;
}) {
  const stdio = row.transport === "stdio";
  const [name, setName] = useState(row.name);
  const [url, setUrl] = useState(row.url ?? "");
  const [command, setCommand] = useState(row.command ?? "");
  const [args, setArgs] = useState(row.args.join("\n"));
  const [pairs, setPairs] = useState("");
  const [clear, setClear] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const kind = stdio ? "env" : "headers";

  const save = () => {
    const bad = nameProblem(name.trim());
    if (bad) return setProblem(bad);
    const body: Record<string, unknown> = {};
    if (name.trim() !== row.name) body.name = name.trim();
    if (stdio) {
      if (command.trim() !== row.command) body.command = command.trim();
      const nextArgs = parseArgs(args);
      if (nextArgs.join("\n") !== row.args.join("\n")) body.args = nextArgs;
    } else if (url.trim() !== row.url) {
      body.url = url.trim();
    }
    if (clear) {
      body[kind] = {};
    } else if (pairs.trim()) {
      const parsed = parsePairs(pairs, kind);
      if (parsed.error !== null) return setProblem(parsed.error);
      body[kind] = parsed.value;
    }
    setProblem(null);
    if (Object.keys(body).length === 0) return setProblem("Nothing has changed.");
    void onSave(body);
  };

  return (
    <div className="border-border mt-3 flex flex-col gap-3 border-t pt-3 text-xs">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`name-${row.id}`}>Name</Label>
        <Input
          id={`name-${row.id}`}
          value={name}
          onChange={(e) => setName(e.target.value.toLowerCase())}
        />
      </div>
      {stdio ? (
        <>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`cmd-${row.id}`}>Command</Label>
            <Input
              id={`cmd-${row.id}`}
              value={command}
              onChange={(e) => setCommand(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`args-${row.id}`}>Arguments, one per line</Label>
            <Textarea
              id={`args-${row.id}`}
              rows={3}
              value={args}
              onChange={(e) => setArgs(e.target.value)}
            />
          </div>
        </>
      ) : (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`url-${row.id}`}>URL</Label>
          <Input id={`url-${row.id}`} value={url} onChange={(e) => setUrl(e.target.value)} />
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`pairs-${row.id}`}>
          Replace {stdio ? "environment variables" : "headers"}
        </Label>
        <Textarea
          id={`pairs-${row.id}`}
          rows={3}
          value={pairs}
          disabled={clear}
          className="font-mono text-xs"
          placeholder={
            (stdio ? row.env_keys : row.header_names).length
              ? "Leave empty to keep the current values"
              : stdio
                ? "API_KEY=…"
                : "Authorization: Bearer …"
          }
          onChange={(e) => setPairs(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
        <p className="text-muted-foreground">
          Values can&apos;t be shown, so enter the full set to replace them.
        </p>
        <label className="flex items-center gap-2">
          <Switch checked={clear} onCheckedChange={setClear} />
          Remove all {stdio ? "environment variables" : "headers"}
        </label>
      </div>
      <p className="text-muted-foreground">
        Changing the {stdio ? "command or arguments" : "URL"} clears the list of discovered tools.
      </p>
      {problem && <p className="text-destructive">{problem}</p>}
      <div className="flex justify-end">
        <Button size="sm" disabled={busy} onClick={save}>
          Save
        </Button>
      </div>
    </div>
  );
}

// ── containment ──────────────────────────────────────────────

function RunnerBanner({ status }: { status: McpRunnerStatus }) {
  if (!status.reachable) {
    return (
      <p className="border-warning bg-warning/10 rounded-md border px-3 py-2 text-xs">
        <strong>Local-command servers can&apos;t run right now.</strong> {status.error} Remote (HTTP
        and SSE) servers are not affected.
      </p>
    );
  }
  const offline = status.network === "none";
  return (
    <p
      className={
        status.full_sandbox
          ? "border-border text-muted-foreground rounded-md border px-3 py-2 text-xs"
          : "border-warning bg-warning/10 rounded-md border px-3 py-2 text-xs"
      }
    >
      <strong>Local-command servers</strong> run in the MCP runner, apart from the rest of the
      platform{offline ? ", with no internet access" : ""}. Protected by:{" "}
      {status.applied.join(", ")}.
      {!status.full_sandbox && (
        <>
          {" "}
          Memory, CPU and process limits are not enforced on this machine ({status.platform}); they
          need the Linux runner container.
        </>
      )}
    </p>
  );
}

const LIMIT_FIELDS: { key: keyof Limits; label: string; unit: string; hint: string }[] = [
  { key: "memory_mb", label: "Memory", unit: "MB", hint: "64 to 8192" },
  {
    key: "cpu_seconds",
    label: "CPU time",
    unit: "seconds",
    hint: "Total over a session, 5 to 7200",
  },
  { key: "max_processes", label: "Processes", unit: "", hint: "Including threads, 4 to 1024" },
  { key: "max_open_files", label: "Open files", unit: "", hint: "16 to 4096" },
  { key: "max_file_mb", label: "Largest file", unit: "MB", hint: "1 to 1024" },
  { key: "wall_clock_s", label: "Session length", unit: "seconds", hint: "10 to 86400" },
  { key: "idle_timeout_s", label: "Idle timeout", unit: "seconds", hint: "10 to 3600" },
];

function LimitsForm({
  limits,
  busy,
  onSave,
}: {
  limits: Limits;
  busy: boolean;
  onSave: (changed: Partial<Limits>) => Promise<void>;
}) {
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(LIMIT_FIELDS.map((f) => [f.key, String(limits[f.key])])),
  );
  const [problem, setProblem] = useState<string | null>(null);

  const save = () => {
    const changed: Partial<Limits> = {};
    for (const f of LIMIT_FIELDS) {
      const n = Number(values[f.key]);
      if (!Number.isInteger(n)) return setProblem(`${f.label} must be a whole number.`);
      if (n !== limits[f.key]) changed[f.key] = n;
    }
    if (Object.keys(changed).length === 0) return setProblem("Nothing has changed.");
    setProblem(null);
    void onSave(changed);
  };

  return (
    <div className="border-border mt-3 flex flex-col gap-3 border-t pt-3 text-xs">
      <p className="text-muted-foreground">
        What this server may use. A session that reaches its length or sits idle is stopped, and
        started again the next time it is needed.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {LIMIT_FIELDS.map((f) => (
          <div key={f.key} className="flex flex-col gap-1.5">
            <Label htmlFor={`lim-${f.key}`}>
              {f.label}
              {f.unit ? ` (${f.unit})` : ""}
            </Label>
            <Input
              id={`lim-${f.key}`}
              type="number"
              value={values[f.key]}
              onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
            />
            <p className="text-muted-foreground">{f.hint}</p>
          </div>
        ))}
      </div>
      {problem && <p className="text-destructive">{problem}</p>}
      <div className="flex justify-end">
        <Button size="sm" disabled={busy} onClick={save}>
          Save limits
        </Button>
      </div>
    </div>
  );
}

// ── checking and discovering ─────────────────────────────────

function CheckOutcome({ kind, result }: { kind: "check" | "discover"; result: McpCheckResult }) {
  const skipped = result.skipped ?? [];
  if (!result.ok) {
    return (
      <p className="text-destructive mt-2 text-xs" role="alert">
        {kind === "check" ? "Check failed" : "Discovery failed"}: {result.error}
      </p>
    );
  }
  return (
    <div className="mt-2 text-xs">
      <p className="text-success">
        {kind === "check"
          ? `Connected in ${result.elapsed_ms} ms.`
          : `Found ${result.tool_count} tool${result.tool_count === 1 ? "" : "s"} in ${result.elapsed_ms} ms.`}
      </p>
      {skipped.length > 0 && (
        <div className="text-muted-foreground mt-1">
          Left out:
          <ul className="ml-4 list-disc">
            {skipped.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ToolList({ tools }: { tools: McpTool[] }) {
  return (
    <div className="border-border mt-3 border-t pt-3 text-xs">
      <p className="text-muted-foreground mb-2">
        What this server offers. Descriptions come from the server and are read by the model, so
        read them before allowing a tool. Choose which tools an assistant may use on its canvas
        node.
      </p>
      <ul className="flex flex-col gap-2">
        {tools.map((tool) => {
          const params = Object.keys(
            (tool.input_schema as { properties?: Record<string, unknown> }).properties ?? {},
          );
          return (
            <li key={tool.name} className="border-border rounded border px-3 py-2">
              <div className="flex flex-wrap items-center gap-2">
                <code className="font-medium">{tool.name}</code>
                {tool.read_only === true && <Badge variant="success">read-only</Badge>}
                {tool.read_only === false && <Badge variant="warning">changes things</Badge>}
                {tool.read_only == null && <Badge variant="muted">not stated</Badge>}
              </div>
              {tool.description && (
                <p className="text-muted-foreground mt-1 whitespace-pre-wrap">{tool.description}</p>
              )}
              {params.length > 0 && (
                <p className="text-muted-foreground mt-1">
                  Inputs: <code>{params.join(", ")}</code>
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ── the catalog (task 4.8) ───────────────────────────────────

function Catalog({
  presets,
  taken,
  onPick,
}: {
  presets: McpPreset[];
  /** Names already registered on this assistant. */
  taken: Set<string>;
  onPick: (preset: McpPreset) => void;
}) {
  return (
    <div className="border-border border-t px-4 py-3">
      <p className="mb-2 text-xs font-medium">Or start from a well-known server</p>
      <ul className="grid gap-2 sm:grid-cols-2">
        {presets.map((p) => {
          const added = taken.has(p.name);
          return (
            <li key={p.key}>
              <button
                type="button"
                disabled={added}
                onClick={() => onPick(p)}
                className="border-border hover:bg-muted flex h-full w-full flex-col items-start gap-1 rounded-md border px-3 py-2 text-left text-xs disabled:cursor-default disabled:opacity-60"
              >
                <span className="flex w-full items-center gap-2">
                  <span className="font-medium">{p.title}</span>
                  <Badge variant="muted">{p.transport === "stdio" ? "local" : p.transport}</Badge>
                  {added && <span className="text-muted-foreground ml-auto">added</span>}
                </span>
                <span className="text-muted-foreground">{p.description}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
