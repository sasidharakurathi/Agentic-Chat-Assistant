"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Lamp } from "@/components/ui/lamp";
import { Loading } from "@/components/ui/loading";
import { SectionHeading } from "@/components/ui/section-heading";
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
import { failureMessage, formatRelative } from "@/lib/format";
import {
  describeLimits,
  missingSecrets,
  nameProblem,
  parseArgs,
  parsePairs,
  type Limits,
} from "@/lib/mcp-forms";
import { cn } from "@/lib/utils";

import { ServerStatus, ToolAccess, TRANSPORT_LABEL } from "./mcp-status";

type Transport = "stdio" | "http" | "sse";

const TRANSPORTS: { value: Transport; label: string; hint: string }[] = [
  {
    value: "http",
    label: "Remote, HTTP (streamable)",
    hint: "A remote server at an https:// URL. The current standard.",
  },
  {
    value: "sse",
    label: "Remote, SSE (older)",
    hint: "A remote server that uses the older server-sent events connection.",
  },
  {
    value: "stdio",
    label: "Local command",
    hint: "A program this platform starts itself, such as an npx or uvx package.",
  },
];

/** Classes for a Fira Code block: commands, headers, tool names. */
const CODE_BLOCK = "bg-muted text-code block rounded-md px-3 py-2 font-mono break-all";

/** The server's own message, or null when it could not be reached. */
function reasonOf(err: unknown): string | null {
  return err instanceof ApiError ? err.message : null;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

type Failure = { what: string; todo: string };
type RunAction = (fn: () => Promise<unknown>, failure: Failure) => Promise<boolean>;

/** The MCP servers tab: register, edit and remove servers, see and set how
 *  local-command servers are contained, and check a server and see the
 *  tools it offers. Choosing which of them an assistant uses happens on its
 *  canvas node, so it is versioned. */
export function McpServersManager({ assistantId }: { assistantId: string }) {
  const [rows, setRows] = useState<McpServer[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [runner, setRunner] = useState<McpRunnerStatus | null>(null);
  const [presets, setPresets] = useState<McpPreset[]>([]);
  const [preset, setPreset] = useState<McpPreset | null>(null);
  const ids = useId();
  const formId = `${ids}-form`;

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
      setError(
        failureMessage(
          "Couldn't load the MCP servers.",
          reasonOf(err),
          "Reload the page to try again.",
        ),
      );
    }
  }, [assistantId]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = useCallback<RunAction>(
    async (fn, failure) => {
      setBusy(true);
      setError(null);
      try {
        await fn();
        await load();
        return true;
      } catch (err) {
        setError(failureMessage(failure.what, reasonOf(err), failure.todo));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  return (
    <div className="min-h-0 flex-1 overflow-auto px-4 py-6 md:px-6 lg:px-8">
      <div className="mx-auto flex w-full max-w-[40rem] flex-col gap-6">
        <SectionHeading
          level={2}
          title="MCP servers"
          description="An MCP server gives the assistant extra tools, such as a ticket system or a code host. Header and environment values are encrypted when saved; afterwards you see only their names."
          actions={
            <Button
              variant={adding ? "outline" : "default"}
              aria-expanded={adding}
              aria-controls={adding ? formId : undefined}
              onClick={() => {
                setPreset(null);
                setAdding((v) => !v);
              }}
            >
              {adding ? "Cancel" : "Add server"}
            </Button>
          }
        />

        {adding && (
          <ServerForm
            key={preset?.key ?? "blank"}
            id={formId}
            preset={preset}
            busy={busy}
            onSubmit={(body) =>
              run(
                async () => {
                  await mcpServers.create(assistantId, body);
                  setAdding(false);
                },
                {
                  what: "Couldn't add the server.",
                  todo: "Check the details, then add it again.",
                },
              )
            }
          />
        )}

        {runner && <RunnerBanner status={runner} />}

        {error && <Alert>{error}</Alert>}

        <section aria-labelledby={`${ids}-list`} className="flex flex-col gap-3">
          <h3 id={`${ids}-list`} className="text-h3 font-semibold">
            Servers
          </h3>
          {rows === null && !error && <Loading what="MCP servers" rows={2} rowHeight={120} />}
          {rows?.length === 0 && (
            <EmptyState
              title="Add your first MCP server"
              description="Add a server, then switch it on for this assistant on the canvas and choose which of its tools it may use."
              action={
                adding ? undefined : (
                  <Button
                    onClick={() => {
                      setPreset(null);
                      setAdding(true);
                    }}
                  >
                    Add server
                  </Button>
                )
              }
            />
          )}
          {rows && rows.length > 0 && (
            <ul className="border-border flex flex-col border-t">
              {rows.map((row) => (
                <ServerRow key={row.id} assistantId={assistantId} row={row} busy={busy} run={run} />
              ))}
            </ul>
          )}
        </section>

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
      </div>
    </div>
  );
}

// ── add ──────────────────────────────────────────────────────

function ServerForm({
  id,
  busy,
  onSubmit,
  preset = null,
}: {
  id: string;
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
  const formRef = useRef<HTMLFormElement>(null);

  // Picking from the catalog (which sits below the list) opens this form at
  // the top: bring it into view and start at its first field.
  useEffect(() => {
    if (preset) formRef.current?.querySelector<HTMLInputElement>("#mcp-name")?.focus();
  }, [preset]);

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
      ref={formRef}
      id={id}
      aria-labelledby={`${id}-title`}
      className="border-border bg-card flex flex-col gap-4 rounded-lg border p-4 sm:p-6"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <h3 id={`${id}-title`} className="text-h3 font-semibold">
        {preset ? `Add ${preset.title}` : "New server"}
      </h3>

      {preset && (
        <div className="bg-muted flex flex-col gap-2 rounded-md px-3 py-2.5 text-sm">
          <p>
            From the catalog.{" "}
            <a
              href={preset.docs_url}
              target="_blank"
              rel="noopener noreferrer"
              className={buttonVariants({ variant: "link", size: "sm" })}
            >
              Read its documentation
              <span className="sr-only"> (opens in a new tab)</span>
            </a>{" "}
            before you add it.
          </p>
          {needed.length > 0 && (
            <div className="flex flex-col gap-1">
              <p className="text-h4 font-semibold">It needs</p>
              <ul className="ml-4 flex list-disc flex-col gap-1">
                {needed.map((f) => (
                  <li key={f.name}>
                    <code className="text-code font-mono">{f.name}</code>: {f.hint}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {preset.needs_network && preset.transport === "stdio" && (
            <p className="text-muted-foreground">
              It downloads its package the first time it runs, so the runner needs internet access.
            </p>
          )}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="mcp-name">Name</Label>
          <Input
            id="mcp-name"
            value={name}
            placeholder="github"
            autoComplete="off"
            spellCheck={false}
            aria-describedby="mcp-name-hint"
            onChange={(e) => setName(e.target.value.toLowerCase())}
          />
          <p id="mcp-name-hint" className="text-small text-muted-foreground">
            Lowercase letters, digits and hyphens, starting with a letter. It becomes part of each
            tool&apos;s name.
          </p>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="mcp-transport">Connection</Label>
          <Select
            id="mcp-transport"
            value={transport}
            aria-describedby="mcp-transport-hint"
            onChange={(e) => setTransport(e.target.value as Transport)}
          >
            {TRANSPORTS.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </Select>
          {hint && (
            <p id="mcp-transport-hint" className="text-small text-muted-foreground">
              {hint}
            </p>
          )}
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
              autoComplete="off"
              spellCheck={false}
              className="text-code font-mono"
              onChange={(e) => setCommand(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mcp-args">Arguments, one per line</Label>
            <Textarea
              id="mcp-args"
              rows={3}
              value={args}
              spellCheck={false}
              className="text-code font-mono"
              aria-describedby="mcp-args-hint"
              placeholder={"-y\n@modelcontextprotocol/server-github"}
              onChange={(e) => setArgs(e.target.value)}
            />
            <p id="mcp-args-hint" className="text-small text-muted-foreground">
              Saved as plain text. Put tokens and keys in the environment variables below instead.
            </p>
          </div>
        </>
      ) : (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="mcp-url">URL</Label>
          <Input
            id="mcp-url"
            type="url"
            inputMode="url"
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
          className="text-code font-mono"
          aria-describedby="mcp-pairs-hint"
          placeholder={stdio ? "GITHUB_TOKEN=ghp_…" : "Authorization: Bearer …"}
          onChange={(e) => setPairs(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
        <p id="mcp-pairs-hint" className="text-small text-muted-foreground">
          Encrypted when saved. Afterwards you see the names, never the values.
        </p>
      </div>

      {problem && <Alert>{problem}</Alert>}
      <div>
        <Button type="submit" disabled={busy}>
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
  run: RunAction;
}) {
  const [editing, setEditing] = useState(false);
  const [limitsOpen, setLimitsOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [result, setResult] = useState<{ kind: "check" | "discover"; r: McpCheckResult } | null>(
    null,
  );
  const [working, setWorking] = useState<"check" | "discover" | null>(null);
  const confirm = useConfirm();
  const base = `mcp-${row.id}`;

  const act = (kind: "check" | "discover") =>
    void (async () => {
      setWorking(kind);
      setResult(null);
      try {
        await run(
          async () => {
            const r =
              kind === "check"
                ? await mcpServers.health(assistantId, row.id)
                : await mcpServers.discover(assistantId, row.id);
            setResult({ kind, r });
            if (kind === "discover" && r.ok) setToolsOpen(true);
          },
          {
            what:
              kind === "check"
                ? `Couldn't check ${row.name}.`
                : `Couldn't list the tools of ${row.name}.`,
            todo: "Try again in a moment.",
          },
        );
      } finally {
        setWorking(null);
      }
    })();
  const stdio = row.transport === "stdio";
  const secretNames = stdio ? row.env_keys : row.header_names;
  const secretWord = stdio ? "Environment variables" : "Headers";

  return (
    <li className="border-border flex flex-col gap-3 border-b py-4">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <span
              id={`${base}-name`}
              className={cn(
                "min-w-0 truncate font-medium",
                !row.enabled && "text-muted-foreground",
              )}
            >
              {row.name}
            </span>
            <ServerStatus server={row} working={working !== null} />
          </div>
          <p className="text-small text-muted-foreground">{TRANSPORT_LABEL[row.transport]}</p>
        </div>
        <label className="text-label flex cursor-pointer items-center gap-2 font-medium">
          <span id={`${base}-on`}>On</span>
          <Switch
            checked={row.enabled}
            disabled={busy}
            aria-labelledby={`${base}-on ${base}-name`}
            onCheckedChange={(v) =>
              void run(() => mcpServers.update(assistantId, row.id, { enabled: v }), {
                what: v ? `Couldn't switch ${row.name} on.` : `Couldn't switch ${row.name} off.`,
                todo: "Try again.",
              })
            }
          />
        </label>
      </div>

      <code className={CODE_BLOCK}>
        {stdio ? [row.command, ...row.args].filter(Boolean).join(" ") : row.url}
      </code>

      <dl className="text-small grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-muted-foreground">{secretWord}</dt>
        <dd className="min-w-0 break-words">
          {secretNames.length > 0 ? (
            <>
              <span className="text-code font-mono">{secretNames.join(", ")}</span>
              <span className="text-muted-foreground"> (values hidden)</span>
            </>
          ) : (
            "None"
          )}
        </dd>
        <dt className="text-muted-foreground">Tools</dt>
        <dd className="min-w-0">
          {row.tools.length > 0 ? (
            <Button
              variant="link"
              size="sm"
              aria-expanded={toolsOpen}
              aria-controls={toolsOpen ? `${base}-tools` : undefined}
              onClick={() => setToolsOpen((v) => !v)}
            >
              {toolsOpen ? "Hide" : "Show"} {plural(row.tools.length, "tool", "tools")}
            </Button>
          ) : (
            "Not discovered yet. Use Discover tools to list them."
          )}
        </dd>
        {row.last_checked_at && (
          <>
            <dt className="text-muted-foreground">Last checked</dt>
            <dd title={new Date(row.last_checked_at).toLocaleString()}>
              {formatRelative(row.last_checked_at)}
            </dd>
          </>
        )}
        {stdio && (
          <>
            <dt className="text-muted-foreground">Limits</dt>
            <dd className="num min-w-0">{describeLimits(row.sandbox as Limits)}</dd>
          </>
        )}
      </dl>

      {row.error && (
        <p className="text-small text-destructive break-words">
          The last check failed: {row.error.trim().replace(/[.!?]+$/, "")}.{" "}
          {stdio
            ? "Check the command, arguments and environment variables, then check again."
            : "Check the URL and headers, then check again."}
        </p>
      )}

      <div className="-ml-3 flex flex-wrap items-center gap-1">
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          title="Connect and complete the MCP handshake"
          onClick={() => act("check")}
        >
          {working === "check" ? "Checking…" : "Check"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          title="Connect and list the tools this server offers"
          onClick={() => act("discover")}
        >
          {working === "discover" ? "Discovering…" : "Discover tools"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={editing}
          aria-controls={editing ? `${base}-edit` : undefined}
          onClick={() => setEditing((v) => !v)}
        >
          {editing ? "Close" : "Edit"}
        </Button>
        {stdio && (
          <Button
            size="sm"
            variant="ghost"
            aria-expanded={limitsOpen}
            aria-controls={limitsOpen ? `${base}-limits` : undefined}
            onClick={() => setLimitsOpen((v) => !v)}
          >
            Limits
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          className="hover:text-destructive focus-visible:text-destructive"
          onClick={() =>
            void (async () => {
              const sure = await confirm({
                title: `Remove "${row.name}"?`,
                description:
                  "Its saved headers or environment variables are deleted too. MCP server stations on the canvas that use it will show a problem until you remove them.",
                confirmLabel: "Remove server",
                destructive: true,
              });
              if (sure)
                void run(() => mcpServers.remove(assistantId, row.id), {
                  what: `Couldn't remove ${row.name}.`,
                  todo: "Try again.",
                });
            })()
          }
        >
          Remove
        </Button>
      </div>

      {result && <CheckOutcome kind={result.kind} result={result.r} stdio={stdio} />}
      {toolsOpen && row.tools.length > 0 && <ToolList id={`${base}-tools`} tools={row.tools} />}
      {limitsOpen && (
        <LimitsForm
          id={`${base}-limits`}
          limits={row.sandbox as Limits}
          busy={busy}
          onSave={async (sandbox) => {
            const ok = await run(() => mcpServers.update(assistantId, row.id, { sandbox }), {
              what: `Couldn't save the limits for ${row.name}.`,
              todo: "Check the values are in range, then save again.",
            });
            if (ok) setLimitsOpen(false);
          }}
        />
      )}
      {editing && (
        <EditForm
          id={`${base}-edit`}
          row={row}
          busy={busy}
          onSave={async (body) => {
            const ok = await run(() => mcpServers.update(assistantId, row.id, body), {
              what: `Couldn't save the changes to ${row.name}.`,
              todo: "Check the details, then save again.",
            });
            if (ok) setEditing(false);
          }}
        />
      )}
    </li>
  );
}

/** A plate that opens under a row: edit, limits. */
const ROW_PLATE = "border-border bg-card flex flex-col gap-4 rounded-lg border p-4 sm:p-6";

function EditForm({
  id,
  row,
  busy,
  onSave,
}: {
  id: string;
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
  const secretWord = stdio ? "environment variables" : "headers";

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
    if (Object.keys(body).length === 0)
      return setProblem("Nothing has changed. Edit a field, then save.");
    void onSave(body);
  };

  return (
    <form
      id={id}
      aria-labelledby={`${id}-title`}
      className={ROW_PLATE}
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <h4 id={`${id}-title`} className="text-h4 font-semibold">
        Edit {row.name}
      </h4>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`name-${row.id}`}>Name</Label>
        <Input
          id={`name-${row.id}`}
          value={name}
          autoComplete="off"
          spellCheck={false}
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
              autoComplete="off"
              spellCheck={false}
              className="text-code font-mono"
              onChange={(e) => setCommand(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`args-${row.id}`}>Arguments, one per line</Label>
            <Textarea
              id={`args-${row.id}`}
              rows={3}
              value={args}
              spellCheck={false}
              className="text-code font-mono"
              onChange={(e) => setArgs(e.target.value)}
            />
          </div>
        </>
      ) : (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`url-${row.id}`}>URL</Label>
          <Input
            id={`url-${row.id}`}
            type="url"
            inputMode="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`pairs-${row.id}`}>Replace {secretWord}</Label>
        <Textarea
          id={`pairs-${row.id}`}
          rows={3}
          value={pairs}
          disabled={clear}
          className="text-code font-mono"
          aria-describedby={`pairs-${row.id}-hint`}
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
        <p id={`pairs-${row.id}-hint`} className="text-small text-muted-foreground">
          Saved values can&apos;t be shown, so enter the full set to replace them.
        </p>
      </div>
      <label className="flex cursor-pointer items-center justify-between gap-4">
        <span id={`${id}-clear`} className="text-sm">
          Remove all {secretWord}
        </span>
        <Switch checked={clear} onCheckedChange={setClear} aria-labelledby={`${id}-clear`} />
      </label>
      <p className="text-small text-muted-foreground">
        Changing the {stdio ? "command or arguments" : "URL"} clears the list of discovered tools.
      </p>
      {problem && <Alert>{problem}</Alert>}
      <div>
        <Button type="submit" disabled={busy}>
          Save changes
        </Button>
      </div>
    </form>
  );
}

// ── containment ──────────────────────────────────────────────

function RunnerBanner({ status }: { status: McpRunnerStatus }) {
  if (!status.reachable) {
    return (
      <Alert tone="warning" title="Local-command servers can't run right now.">
        {status.error ? `${status.error.trim().replace(/[.!?]+$/, "")}. ` : ""}
        Remote servers (HTTP and SSE) still work. Reload this page once the runner is running.
      </Alert>
    );
  }
  const offline = status.network === "none";
  return (
    <Alert
      tone={status.full_sandbox ? "neutral" : "warning"}
      title="How local-command servers are contained"
    >
      They run in the MCP runner, apart from the rest of the platform
      {offline ? ", with no internet access" : ""}.
      {status.applied.length > 0 && <> Protected by: {status.applied.join(", ")}.</>}
      {!status.full_sandbox && (
        <>
          {" "}
          Memory, CPU and process limits are not enforced on this server
          {status.platform ? ` (${status.platform})` : ""}. They are enforced only when the runner
          runs in its Linux container.
        </>
      )}
    </Alert>
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
  id,
  limits,
  busy,
  onSave,
}: {
  id: string;
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
      if (!Number.isInteger(n))
        return setProblem(`${f.label} must be a whole number. Enter one, then save.`);
      if (n !== limits[f.key]) changed[f.key] = n;
    }
    if (Object.keys(changed).length === 0)
      return setProblem("Nothing has changed. Edit a limit, then save.");
    setProblem(null);
    void onSave(changed);
  };

  return (
    <form
      id={id}
      aria-labelledby={`${id}-title`}
      className={ROW_PLATE}
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <SectionHeading
        level={4}
        id={`${id}-title`}
        title="Limits"
        description="What this server may use. A session that reaches its length or sits idle is stopped, and started again the next time it is needed."
      />
      <div className="grid gap-4 sm:grid-cols-2">
        {LIMIT_FIELDS.map((f) => (
          <div key={f.key} className="flex flex-col gap-1.5">
            <Label htmlFor={`lim-${f.key}`}>
              {f.label}
              {f.unit ? ` (${f.unit})` : ""}
            </Label>
            <Input
              id={`lim-${f.key}`}
              type="number"
              className="num"
              aria-describedby={`lim-${f.key}-hint`}
              value={values[f.key]}
              onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
            />
            <p id={`lim-${f.key}-hint`} className="text-small text-muted-foreground num">
              {f.hint}
            </p>
          </div>
        ))}
      </div>
      {problem && <Alert>{problem}</Alert>}
      <div>
        <Button type="submit" disabled={busy}>
          Save limits
        </Button>
      </div>
    </form>
  );
}

// ── checking and discovering ─────────────────────────────────

function CheckOutcome({
  kind,
  result,
  stdio,
}: {
  kind: "check" | "discover";
  result: McpCheckResult;
  stdio: boolean;
}) {
  const skipped = result.skipped ?? [];
  if (!result.ok) {
    const why = result.error?.trim().replace(/[.!?]+$/, "");
    return (
      <Alert title={kind === "check" ? "Check failed." : "Couldn't list the tools."}>
        {why ? `${why}. ` : ""}
        {stdio
          ? "Check the command, arguments and environment variables, then try again."
          : "Check the URL and headers, then try again."}
      </Alert>
    );
  }
  return (
    <div className="text-small flex flex-col gap-1" role="status">
      <p className="flex items-center gap-2">
        <Lamp tone="success" />
        <span className="num">
          {kind === "check"
            ? `Connected in ${result.elapsed_ms} ms.`
            : `Found ${plural(result.tool_count ?? 0, "tool", "tools")} in ${result.elapsed_ms} ms.`}
        </span>
      </p>
      {skipped.length > 0 && (
        <div className="text-muted-foreground pl-4">
          <p>Left out:</p>
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

function ToolList({ id, tools }: { id: string; tools: McpTool[] }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="flex flex-col gap-2">
      <SectionHeading
        level={4}
        id={`${id}-title`}
        title="Tools this server offers"
        description="Descriptions come from the server and are read by the model, so read them before allowing a tool. Choose which tools an assistant may use on its canvas station."
      />
      <ul className="border-border flex flex-col border-t">
        {tools.map((tool) => {
          const params = Object.keys(
            (tool.input_schema as { properties?: Record<string, unknown> }).properties ?? {},
          );
          return (
            <li
              key={tool.name}
              className="border-border flex flex-col gap-1.5 border-b py-3 last:border-b-0 last:pb-0"
            >
              <div className="flex flex-wrap items-center gap-2">
                <code className="text-code min-w-0 font-mono font-medium break-all">
                  {tool.name}
                </code>
                <ToolAccess readOnly={tool.read_only} />
              </div>
              {tool.description && (
                <p className="text-small text-muted-foreground max-w-[68ch] whitespace-pre-wrap">
                  {tool.description}
                </p>
              )}
              {params.length > 0 && (
                <p className="text-small text-muted-foreground">
                  Inputs:{" "}
                  <code className="text-code text-foreground font-mono break-all">
                    {params.join(", ")}
                  </code>
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// ── the catalog ──────────────────────────────────────────────

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
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <SectionHeading
        level={3}
        id={id}
        title="Start from a well-known server"
        description="Fills in the form for you. You still add your own keys."
      />
      <ul className="grid gap-2 sm:grid-cols-2">
        {presets.map((p) => {
          const added = taken.has(p.name);
          return (
            <li key={p.key}>
              <button
                type="button"
                disabled={added}
                onClick={() => onPick(p)}
                className="border-field-border bg-card hover:bg-muted focus-visible:ring-ring focus-visible:ring-offset-background disabled:hover:bg-card flex h-full w-full flex-col items-start gap-1 rounded-md border px-3 py-2.5 text-left transition-colors duration-120 ease-out focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none disabled:cursor-default disabled:opacity-60"
              >
                <span className="flex w-full flex-wrap items-center gap-2">
                  <span className="font-medium">{p.title}</span>
                  <Badge variant="muted">{TRANSPORT_LABEL[p.transport]}</Badge>
                  {added && (
                    <span className="text-small text-muted-foreground ml-auto">Already added</span>
                  )}
                </span>
                <span className="text-small text-muted-foreground">{p.description}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
