"use client";

import { useCallback, useEffect, useId, useState } from "react";

import { Toggle } from "@/components/config/panels";
import { LeastPrivilege } from "@/components/databases/LeastPrivilege";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Lamp } from "@/components/ui/lamp";
import { Loading } from "@/components/ui/loading";
import { SectionHeading } from "@/components/ui/section-heading";
import { Select } from "@/components/ui/select";
import {
  ApiError,
  dbConnections,
  type DbConnection,
  type DbEngine,
  type DbSchema,
} from "@/lib/api";
import { failureMessage } from "@/lib/format";

const ENGINES: { value: DbEngine; label: string; port: number | "" }[] = [
  { value: "postgres", label: "PostgreSQL", port: 5432 },
  { value: "mysql", label: "MySQL / MariaDB", port: 3306 },
  { value: "mongodb", label: "MongoDB", port: 27017 },
  { value: "sqlite", label: "SQLite (file)", port: "" },
];

const ENGINE_NAME: Record<DbEngine, string> = {
  postgres: "PostgreSQL",
  mysql: "MySQL",
  mongodb: "MongoDB",
  sqlite: "SQLite",
};

/** A failed action, ready to show: what failed, why, what to do. */
type Failure = { what: string; todo: string };
type RunAction = (fn: () => Promise<unknown>, failure: Failure) => Promise<boolean>;

/** The server's own message, or null when it could not be reached. */
function reasonOf(err: unknown): string | null {
  return err instanceof ApiError ? err.message : null;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

export function DatabasesManager({ assistantId }: { assistantId: string }) {
  const [rows, setRows] = useState<DbConnection[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const formId = useId();

  const load = useCallback(async () => {
    try {
      setRows(await dbConnections.list(assistantId));
    } catch (err) {
      setError(
        failureMessage(
          "Couldn't load the database connections.",
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
          title="Database connections"
          description={
            <>
              Databases the assistant can query. Connect with a read-only role made for the
              assistant, not an application account. New connections are read-only and return at
              most 500 rows per query.
            </>
          }
          actions={
            <Button
              variant={adding ? "outline" : "default"}
              aria-expanded={adding}
              aria-controls={adding ? formId : undefined}
              onClick={() => setAdding((v) => !v)}
            >
              {adding ? "Cancel" : "Add connection"}
            </Button>
          }
        />

        {adding && (
          <ConnectionForm
            id={formId}
            busy={busy}
            onSubmit={(body) =>
              void run(
                async () => {
                  await dbConnections.create(assistantId, body);
                  setAdding(false);
                },
                {
                  what: "Couldn't add the connection.",
                  todo: "Check the details, then add it again.",
                },
              )
            }
          />
        )}

        {error && <Alert>{error}</Alert>}

        <section aria-labelledby={`${formId}-list`} className="flex flex-col gap-3">
          <h3 id={`${formId}-list`} className="text-h3 font-semibold">
            Connections
          </h3>
          {rows === null && !error && <Loading what="connections" rows={2} rowHeight={88} />}
          {rows?.length === 0 && (
            <EmptyState
              title="Connect your first database"
              description="Add a connection, then wire it to the agent on the canvas so the assistant can query it."
              action={
                adding ? undefined : <Button onClick={() => setAdding(true)}>Add connection</Button>
              }
            />
          )}
          {rows && rows.length > 0 && (
            <ul className="border-border flex flex-col border-t">
              {rows.map((row) => (
                <ConnectionRow
                  key={row.id}
                  assistantId={assistantId}
                  row={row}
                  busy={busy}
                  run={run}
                />
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

function ConnectionForm({
  id,
  busy,
  onSubmit,
}: {
  id: string;
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
  // A connection string replaces host/port/credentials entirely, and is the
  // only way to express e.g. an Atlas `mongodb+srv://` cluster.
  const byUri = isMongo && connectionUri.trim() !== "";
  const canSubmit = !busy && name.trim() !== "" && database.trim() !== "";

  const submit = () => {
    if (!canSubmit) return;
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
    );
  };

  return (
    <form
      id={id}
      aria-labelledby={`${id}-title`}
      className="border-border bg-card flex flex-col gap-4 rounded-lg border p-4 sm:p-6"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <h3 id={`${id}-title`} className="text-h3 font-semibold">
        New connection
      </h3>
      <div className="grid gap-4 sm:grid-cols-2">
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
            aria-describedby="db-uri-hint"
            value={connectionUri}
            onChange={(e) => setConnectionUri(e.target.value)}
          />
          <p id="db-uri-hint" className="text-small text-muted-foreground max-w-[60ch]">
            Use this instead of a host and password. It is encrypted when saved and never shown
            again.
          </p>
        </div>
      )}

      {!isFile && !byUri && (
        <div className="grid gap-4 sm:grid-cols-[2fr_1fr]">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="db-host">Host</Label>
            <Input id="db-host" value={host} onChange={(e) => setHost(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="db-port">Port</Label>
            <Input
              id="db-port"
              inputMode="numeric"
              className="num"
              value={port}
              onChange={(e) => setPort(e.target.value)}
            />
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
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="db-user">Username</Label>
            <Input
              id="db-user"
              autoComplete="off"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="db-pass">Password</Label>
            <Input
              id="db-pass"
              type="password"
              autoComplete="new-password"
              aria-describedby="db-pass-hint"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {/* Stored envelope-encrypted and never returned by any endpoint. */}
            <p id="db-pass-hint" className="text-small text-muted-foreground">
              Encrypted when saved and never shown again.
            </p>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={!canSubmit}>
          Add connection
        </Button>
        {!canSubmit && !busy && (
          <p className="text-small text-muted-foreground">
            Fill in a name and {isFile ? "a file path" : "a database"} to add it.
          </p>
        )}
      </div>
    </form>
  );
}

/** "PostgreSQL at localhost:5432, database appdb, signs in as assistant_ro
 *  with a saved password." Plain words instead of a dotted meta string. */
function describeConnection(row: DbConnection): string {
  const parts: string[] = [];
  const where = row.host ? ` at ${row.host}${row.port ? `:${row.port}` : ""}` : "";
  parts.push(`${ENGINE_NAME[row.engine]}${where}`);
  parts.push(row.engine === "sqlite" ? `file ${row.database}` : `database ${row.database}`);
  if (row.username) {
    parts.push(`signs in as ${row.username}${row.has_password ? " with a saved password" : ""}`);
  } else if (row.has_password) {
    parts.push("saved password");
  }
  if (row.has_connection_uri) parts.push("uses a saved connection string");
  return `${parts.join(", ")}.`;
}

function ConnectionStatus({ row, checking }: { row: DbConnection; checking: boolean }) {
  if (checking)
    return (
      <Badge variant="muted" live>
        Checking
      </Badge>
    );
  if (row.status === "ok") return <Badge variant="success">Connected</Badge>;
  if (row.status === "error") return <Badge variant="destructive">Failed</Badge>;
  return (
    <Badge variant="muted" lamp={false}>
      <Lamp tone="off" />
      Not checked
    </Badge>
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
  run: RunAction;
}) {
  const [open, setOpen] = useState(false);
  const [testing, setTesting] = useState(false);
  const [schema, setSchema] = useState<DbSchema | null>(null);
  const confirm = useConfirm();
  const perms = row.permissions;
  const panelId = `perms-${row.id}`;
  const mongo = row.engine === "mongodb";

  // The server drops its cached schema on any permission change (the cache
  // only ever held what the *old* profile allowed), so the count shown here
  // is cleared too rather than left describing the previous rules.
  const setPerm = (patch: Partial<typeof perms>) =>
    void run(
      async () => {
        await dbConnections.update(assistantId, row.id, { permissions: { ...perms, ...patch } });
        setSchema(null);
      },
      {
        what: `Couldn't save the permissions for ${row.name}.`,
        todo: "Try the change again.",
      },
    );
  const noun = mongo ? "collections" : "tables";
  const splitList = (v: string) =>
    v
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
  const visible = schema?.schemas.reduce((n, s) => n + s.tables.length, 0) ?? 0;

  return (
    <li className="border-border flex flex-col gap-3 border-b py-4">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 truncate font-medium">{row.name}</span>
            <ConnectionStatus row={row} checking={testing} />
            {perms.write ? (
              <Badge variant="warning">Can change data</Badge>
            ) : (
              <Badge variant="muted">Read-only</Badge>
            )}
          </div>
          <p className="text-small text-muted-foreground break-words">{describeConnection(row)}</p>
          {row.error && (
            <p className="text-small text-destructive break-words">
              The last test failed: {row.error.trim().replace(/[.!?]+$/, "")}. Check the host,
              credentials and network, then test again.
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            aria-expanded={open}
            aria-controls={open ? panelId : undefined}
            onClick={() => setOpen((v) => !v)}
          >
            Permissions
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() =>
              void (async () => {
                setTesting(true);
                try {
                  await run(() => dbConnections.test(assistantId, row.id), {
                    what: `Couldn't test ${row.name}.`,
                    todo: "Check the connection details, then test again.",
                  });
                } finally {
                  setTesting(false);
                }
              })()
            }
          >
            {testing ? "Testing…" : "Test connection"}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            className="hover:text-destructive focus-visible:text-destructive"
            onClick={() =>
              void (async () => {
                const sure = await confirm({
                  title: `Delete "${row.name}"?`,
                  description:
                    "Its saved password or connection string is deleted too. Database stations on the canvas that use it will show a problem until you pick another connection.",
                  confirmLabel: "Delete connection",
                  destructive: true,
                });
                if (sure)
                  void run(() => dbConnections.remove(assistantId, row.id), {
                    what: `Couldn't delete ${row.name}.`,
                    todo: "Try again.",
                  });
              })()
            }
          >
            Delete
          </Button>
        </div>
      </div>

      {open && (
        <div
          id={panelId}
          className="border-border bg-card flex flex-col gap-4 rounded-lg border p-4 sm:p-6"
        >
          <SectionHeading
            level={4}
            title="Permissions"
            description="Checked on every statement the assistant runs. Writes and schema changes also need someone to approve them in chat."
          />
          <div className="border-border divide-border flex flex-col divide-y border-y">
            <div className="py-2">
              <Toggle
                label={mongo ? "Allow reads (find, aggregate)" : "Allow reads (SELECT)"}
                checked={perms.read}
                hint={!perms.read ? "Every query on this connection will be refused." : undefined}
                tone={!perms.read ? "warning" : undefined}
                onChange={(v) => setPerm({ read: v })}
              />
            </div>
            <div className="py-2">
              <Toggle
                label={
                  mongo
                    ? "Allow writes (insert, update, delete)"
                    : "Allow writes (INSERT, UPDATE, DELETE)"
                }
                checked={perms.write}
                onChange={(v) => setPerm({ write: v, ddl: v ? perms.ddl : false })}
              />
            </div>
            <div className="py-2">
              <Toggle
                label={
                  mongo
                    ? "Allow schema changes (create or drop collections)"
                    : "Allow schema changes (CREATE, ALTER, DROP)"
                }
                checked={perms.ddl}
                disabled={!perms.write}
                hint={!perms.write ? "Allow writes first." : undefined}
                onChange={(v) => setPerm({ ddl: v })}
              />
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`rl-${row.id}`}>Row limit</Label>
              <Input
                id={`rl-${row.id}`}
                type="number"
                className="num"
                aria-describedby={`rl-${row.id}-hint`}
                defaultValue={String(perms.row_limit)}
                onBlur={(e) => setPerm({ row_limit: Number(e.target.value) || 500 })}
              />
              <p id={`rl-${row.id}-hint`} className="text-small text-muted-foreground">
                The most rows one query returns.
              </p>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`to-${row.id}`}>Statement timeout (ms)</Label>
              <Input
                id={`to-${row.id}`}
                type="number"
                className="num"
                aria-describedby={`to-${row.id}-hint`}
                defaultValue={String(perms.statement_timeout_ms)}
                onBlur={(e) => setPerm({ statement_timeout_ms: Number(e.target.value) || 10000 })}
              />
              <p id={`to-${row.id}-hint`} className="text-small text-muted-foreground">
                A query that runs longer is stopped.
              </p>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`allow-${row.id}`}>Only these {noun}</Label>
            <Input
              id={`allow-${row.id}`}
              aria-describedby={`allow-${row.id}-hint`}
              defaultValue={perms.allow_tables.join(", ")}
              placeholder="orders, customers"
              onBlur={(e) => setPerm({ allow_tables: splitList(e.target.value) })}
            />
            <p
              id={`allow-${row.id}-hint`}
              className="text-small text-muted-foreground max-w-[60ch]"
            >
              Separate names with commas. Leave empty to allow all. When set, nothing outside this
              list can be queried or seen.
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`deny-${row.id}`}>Hidden {noun}</Label>
            <Input
              id={`deny-${row.id}`}
              aria-describedby={`deny-${row.id}-hint`}
              defaultValue={perms.deny_tables.join(", ")}
              placeholder={mongo ? "audit_log, api_keys" : "salaries, api_keys"}
              onBlur={(e) => setPerm({ deny_tables: splitList(e.target.value) })}
            />
            {/* Denied tables are filtered out of introspection too: they are
                never cached and never reach the model's context. */}
            <p id={`deny-${row.id}-hint`} className="text-small text-muted-foreground max-w-[60ch]">
              Separate names with commas. These are left out of the schema the assistant sees, not
              only refused in queries.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                void run(
                  async () => {
                    setSchema(await dbConnections.refreshSchema(assistantId, row.id));
                  },
                  {
                    what: `Couldn't read the schema of ${row.name}.`,
                    todo: "Test the connection, then try again.",
                  },
                )
              }
            >
              Refresh schema
            </Button>
            <span className="text-small text-muted-foreground" aria-live="polite">
              {schema &&
                `${plural(visible, mongo ? "collection" : "table", noun)} visible to the assistant.${
                  schema.filtered ? " Some are hidden by these permissions." : ""
                }`}
            </span>
          </div>
        </div>
      )}
    </li>
  );
}
