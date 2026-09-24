"use client";

import { useCallback, useEffect, useState } from "react";

import { LeastPrivilege } from "@/components/databases/LeastPrivilege";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  ApiError,
  dbConnections,
  type DbConnection,
  type DbEngine,
  type DbSchema,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const ENGINES: { value: DbEngine; label: string; port: number | "" }[] = [
  { value: "postgres", label: "PostgreSQL", port: 5432 },
  { value: "mysql", label: "MySQL / MariaDB", port: 3306 },
  { value: "mongodb", label: "MongoDB", port: 27017 },
  { value: "sqlite", label: "SQLite (file)", port: "" },
];

function message(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : "Something went wrong";
}

export function DatabasesManager({ assistantId }: { assistantId: string }) {
  const [rows, setRows] = useState<DbConnection[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      setRows(await dbConnections.list(assistantId));
    } catch (err) {
      setError(message(err));
    }
  }, [assistantId]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = useCallback(
    async (fn: () => Promise<unknown>) => {
      setBusy(true);
      setError(null);
      try {
        await fn();
        await load();
      } catch (err) {
        setError(message(err));
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
          <h2 className="text-sm font-semibold">Database connections</h2>
          <Button size="sm" variant="outline" onClick={() => setAdding((v) => !v)}>
            {adding ? "Cancel" : "Add connection"}
          </Button>
        </div>
        {adding && (
          <ConnectionForm
            busy={busy}
            onSubmit={(body) =>
              void run(async () => {
                await dbConnections.create(assistantId, body);
                setAdding(false);
              })
            }
          />
        )}
        {!adding && (
          <p className="text-muted-foreground px-4 py-3 text-xs">
            Create a <strong>read-only role</strong> for the assistant rather than reusing an
            application account. <strong>Add connection</strong> shows the exact role to create for
            each engine. New connections default to read-only with a 500-row cap.
          </p>
        )}
      </section>

      {error && (
        <p className="text-destructive text-sm" role="alert">
          {error}
        </p>
      )}

      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold">Connections{rows ? ` (${rows.length})` : ""}</h2>
        {rows === null && <p className="text-muted-foreground text-sm">Loading…</p>}
        {rows?.length === 0 && (
          <p className="text-muted-foreground border-border rounded-md border border-dashed px-4 py-8 text-center text-sm">
            No databases yet. Add one above, then wire it to the agent on the Canvas.
          </p>
        )}
        <ul className="flex flex-col gap-2">
          {(rows ?? []).map((row) => (
            <ConnectionRow key={row.id} assistantId={assistantId} row={row} busy={busy} run={run} />
          ))}
        </ul>
      </section>
    </div>
  );
}

function ConnectionForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (body: Record<string, unknown>) => void;
}) {
  const [engine, setEngine] = useState<DbEngine>("postgres");
  const [name, setName] = useState("");
  const [host, setHost] = useState("localhost");
  const [port, setPort] = useState<string>("5432");
  const [database, setDatabase] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [connectionUri, setConnectionUri] = useState("");

  const isFile = engine === "sqlite";
  const isMongo = engine === "mongodb";
  // A connection string replaces host/port/credentials entirely — and is the
  // only way to express e.g. an Atlas `mongodb+srv://` cluster.
  const byUri = isMongo && connectionUri.trim() !== "";

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="db-engine">Engine</Label>
          <Select
            id="db-engine"
            value={engine}
            onChange={(e) => {
              const next = e.target.value as DbEngine;
              setEngine(next);
              setPort(String(ENGINES.find((x) => x.value === next)?.port ?? ""));
            }}
          >
            {ENGINES.map((x) => (
              <option key={x.value} value={x.value}>
                {x.label}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="db-name">Name</Label>
          <Input
            id="db-name"
            placeholder="Analytics replica"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
      </div>

      <LeastPrivilege engine={engine} />

      {isMongo && (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="db-uri">Connection string (optional)</Label>
          <Input
            id="db-uri"
            type="password"
            autoComplete="off"
            placeholder="mongodb+srv://user:pass@cluster.example.net"
            value={connectionUri}
            onChange={(e) => setConnectionUri(e.target.value)}
          />
          <p className="text-muted-foreground text-xs">
            Use instead of host and password. Encrypted at rest like a password; never shown again.
          </p>
        </div>
      )}

      {!isFile && !byUri && (
        <div className="grid grid-cols-[2fr_1fr] gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="db-host">Host</Label>
            <Input id="db-host" value={host} onChange={(e) => setHost(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="db-port">Port</Label>
            <Input id="db-port" value={port} onChange={(e) => setPort(e.target.value)} />
          </div>
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="db-database">{isFile ? "File path" : "Database"}</Label>
        <Input
          id="db-database"
          placeholder={isFile ? "/data/app.db" : "appdb"}
          value={database}
          onChange={(e) => setDatabase(e.target.value)}
        />
      </div>

      {!isFile && !byUri && (
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="db-user">Username</Label>
            <Input id="db-user" value={username} onChange={(e) => setUsername(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="db-pass">Password</Label>
            <Input
              id="db-pass"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {/* Stored envelope-encrypted and never returned by any endpoint. */}
            <p className="text-muted-foreground text-xs">Encrypted at rest; never shown again.</p>
          </div>
        </div>
      )}

      <div>
        <Button
          size="sm"
          disabled={busy || !name.trim() || !database.trim()}
          onClick={() =>
            onSubmit(
              byUri
                ? {
                    name: name.trim(),
                    engine,
                    database: database.trim(),
                    connection_uri: connectionUri.trim(),
                  }
                : {
                    name: name.trim(),
                    engine,
                    database: database.trim(),
                    host: isFile ? null : host.trim(),
                    port: isFile || !port ? null : Number(port),
                    username: isFile ? null : username.trim() || null,
                    password: isFile ? null : password || null,
                  },
            )
          }
        >
          Add connection
        </Button>
      </div>
    </div>
  );
}

function ConnectionRow({
  assistantId,
  row,
  busy,
  run,
}: {
  assistantId: string;
  row: DbConnection;
  busy: boolean;
  run: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [schema, setSchema] = useState<DbSchema | null>(null);
  const confirm = useConfirm();
  const perms = row.permissions;

  // The server drops its cached schema on any permission change (the cache
  // only ever held what the *old* profile allowed), so the count shown here
  // is cleared too rather than left describing the previous rules.
  const setPerm = (patch: Partial<typeof perms>) =>
    void run(async () => {
      await dbConnections.update(assistantId, row.id, { permissions: { ...perms, ...patch } });
      setSchema(null);
    });
  const noun = row.engine === "mongodb" ? "collections" : "tables";
  const splitList = (v: string) =>
    v
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);

  return (
    <li className="border-border rounded-md border px-4 py-3">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{row.name}</span>
            <Badge
              variant={
                row.status === "ok" ? "success" : row.status === "error" ? "destructive" : "muted"
              }
            >
              {row.status}
            </Badge>
            {perms.write ? (
              <Badge variant="warning">writable</Badge>
            ) : (
              <Badge variant="muted">read-only</Badge>
            )}
          </div>
          <p className="text-muted-foreground mt-1 truncate text-xs">
            {row.engine}
            {row.host ? ` · ${row.host}${row.port ? `:${row.port}` : ""}` : ""} · {row.database}
            {row.username ? ` · ${row.username}` : ""}
            {row.has_password ? " · password set" : ""}
            {row.has_connection_uri ? " · connection string set" : ""}
          </p>
          {row.error && <p className="text-destructive mt-1 text-xs">{row.error}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button size="sm" variant="outline" disabled={busy} onClick={() => setOpen((v) => !v)}>
            Permissions
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void run(() => dbConnections.test(assistantId, row.id))}
          >
            Test
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() =>
              void (async () => {
                const sure = await confirm({
                  title: `Delete "${row.name}"?`,
                  description: "Its stored credential is removed too.",
                  confirmLabel: "Delete",
                  destructive: true,
                });
                if (sure) void run(() => dbConnections.remove(assistantId, row.id));
              })()
            }
          >
            Delete
          </Button>
        </div>
      </div>

      {open && (
        <div className="border-border mt-3 flex flex-col gap-3 border-t pt-3 text-xs">
          <p className="text-muted-foreground">
            Enforced by the query guard on every statement — not advisory. Writes and schema changes
            additionally require human approval in chat.
          </p>
          <Toggle
            label={
              row.engine === "mongodb" ? "Allow reads (find / aggregate)" : "Allow reads (SELECT)"
            }
            checked={perms.read}
            hint={!perms.read ? "Every query on this connection will be refused." : undefined}
            onChange={(v) => setPerm({ read: v })}
          />
          <Toggle
            label="Allow writes (INSERT / UPDATE / DELETE)"
            checked={perms.write}
            onChange={(v) => setPerm({ write: v, ddl: v ? perms.ddl : false })}
          />
          <Toggle
            label="Allow schema changes (CREATE / ALTER / DROP)"
            checked={perms.ddl}
            disabled={!perms.write}
            hint={!perms.write ? "Requires writes to be allowed first." : undefined}
            onChange={(v) => setPerm({ ddl: v })}
          />
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`rl-${row.id}`}>Row limit</Label>
              <Input
                id={`rl-${row.id}`}
                type="number"
                defaultValue={String(perms.row_limit)}
                onBlur={(e) => setPerm({ row_limit: Number(e.target.value) || 500 })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`to-${row.id}`}>Statement timeout (ms)</Label>
              <Input
                id={`to-${row.id}`}
                type="number"
                defaultValue={String(perms.statement_timeout_ms)}
                onBlur={(e) => setPerm({ statement_timeout_ms: Number(e.target.value) || 10000 })}
              />
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`allow-${row.id}`}>Allowed {noun} only (comma separated)</Label>
            <Input
              id={`allow-${row.id}`}
              defaultValue={perms.allow_tables.join(", ")}
              placeholder="orders, customers"
              onBlur={(e) => setPerm({ allow_tables: splitList(e.target.value) })}
            />
            {/* The allow-list was enforced and documented in
                DATABASE_ACCESS.md, but there was no way to set it here. */}
            <p className="text-muted-foreground">
              Leave empty to allow all. When set, nothing outside this list can be queried or seen.
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`deny-${row.id}`}>Denied {noun} (comma separated)</Label>
            <Input
              id={`deny-${row.id}`}
              defaultValue={perms.deny_tables.join(", ")}
              placeholder={row.engine === "mongodb" ? "audit_log, api_keys" : "salaries, api_keys"}
              onBlur={(e) => setPerm({ deny_tables: splitList(e.target.value) })}
            />
            {/* Denied tables are filtered out of introspection too — they are
                never cached and never reach the model's context. */}
            <p className="text-muted-foreground">
              Hidden from the schema the assistant sees, not just from queries.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  setSchema(await dbConnections.refreshSchema(assistantId, row.id));
                })
              }
            >
              Refresh schema
            </Button>
            {schema && (
              <span className="text-muted-foreground">
                {schema.schemas.reduce((n, s) => n + s.tables.length, 0)} visible table(s)
                {schema.filtered ? " (some hidden by permissions)" : ""}
              </span>
            )}
          </div>
        </div>
      )}
    </li>
  );
}

function Toggle({
  label,
  checked,
  disabled,
  hint,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  hint?: string;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className={cn("flex flex-col gap-0.5", disabled && "opacity-60")}>
      <label className="flex items-center justify-between gap-3">
        <span>{label}</span>
        <Switch checked={checked} disabled={disabled} onCheckedChange={onChange} />
      </label>
      {hint && <span className="text-muted-foreground">{hint}</span>}
    </div>
  );
}
