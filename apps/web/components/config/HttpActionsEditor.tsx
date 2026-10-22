"use client";

import { Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useId, useState } from "react";

import { ParamsFieldset } from "@/components/config/ActionsEditor";
import { Toggle } from "@/components/config/panels";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  approvals,
  httpConnections,
  type HttpAction,
  type HttpConnection,
} from "@/lib/api";
import { actionName, TIER_WORDS, type Tier } from "@/lib/actions";

/** Calls to company systems (Phase 9.3), in the HTTP request tool's drawer.
 *
 *  A **connection** is a system's address and the headers it needs (an API
 *  key, a token), sealed on the server: once saved, only their names come
 *  back. An **action** is one call on it: a method and a path with {slots},
 *  whose values the model fills in. The platform adds who is asking when
 *  the connection says so. */

type Method = HttpAction["method"];
const METHODS: Method[] = ["GET", "POST", "PUT", "PATCH", "DELETE"];

const EMPTY_ACTION: HttpAction = {
  name: "",
  title: "",
  description: "",
  connection_id: "",
  method: "POST",
  path: "/",
  params: [],
  tier: "approver",
  fields: [],
};

export function HttpActionsEditor({
  assistantId,
  actions,
  onChange,
}: {
  assistantId: string;
  actions: HttpAction[];
  onChange: (next: HttpAction[]) => void;
}) {
  const [connections, setConnections] = useState<HttpConnection[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ index: number | null; draft: HttpAction } | null>(null);

  const load = useCallback(async () => {
    try {
      setConnections(await httpConnections.list(assistantId));
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Couldn't load the connections.");
    }
  }, [assistantId]);
  useEffect(() => {
    void load();
  }, [load]);

  const byId = new Map((connections ?? []).map((c) => [c.id, c]));

  return (
    <div className="flex flex-col gap-6">
      <ConnectionsSection
        assistantId={assistantId}
        connections={connections}
        usedBy={(id) => actions.filter((a) => a.connection_id === id).map((a) => a.title)}
        onChanged={load}
      />
      {loadError && <Alert role="alert">{loadError}</Alert>}

      <section className="flex flex-col gap-3" aria-label="Calls">
        <div>
          <p className="font-medium">Calls</p>
          <p className="text-small text-muted-foreground">
            Named requests the assistant can make, with values it fills in. Who asks can go in the
            path as {"{asker_email}"}, {"{asker_name}"} or {"{asker_id}"}.
          </p>
        </div>
        {actions.length > 0 && (
          <ul className="border-border border-t">
            {actions.map((a, i) => (
              <li key={a.name} className="border-border flex items-center gap-2 border-b py-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate" title={a.title}>
                    {a.title}
                  </p>
                  <p className="text-small text-muted-foreground truncate font-mono">
                    {a.method ?? "POST"} {a.path} on{" "}
                    {byId.get(a.connection_id)?.name ?? "a missing connection"}
                  </p>
                </div>
                <Badge variant={a.tier === "read" ? "muted" : "warning"}>
                  {TIER_WORDS[(a.tier ?? "approver") as Tier]}
                </Badge>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setEditing({ index: i, draft: a })}
                >
                  Edit
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove ${a.title}`}
                  onClick={() => onChange(actions.filter((_, j) => j !== i))}
                >
                  <Trash2 aria-hidden className="size-4" />
                </Button>
              </li>
            ))}
          </ul>
        )}
        {editing ? (
          <CallForm
            assistantId={assistantId}
            connections={connections ?? []}
            initial={editing.draft}
            taken={actions.filter((_, j) => j !== editing.index).map((a) => a.name)}
            onCancel={() => setEditing(null)}
            onSave={(saved) => {
              const next = [...actions];
              if (editing.index === null) next.push(saved);
              else next[editing.index] = saved;
              onChange(next);
              setEditing(null);
            }}
          />
        ) : (
          <Button
            size="sm"
            variant="outline"
            className="self-start"
            disabled={!connections?.length}
            onClick={() =>
              setEditing({
                index: null,
                draft: { ...EMPTY_ACTION, connection_id: connections?.[0]?.id ?? "", params: [] },
              })
            }
          >
            <Plus aria-hidden className="size-4" />
            Add a call
          </Button>
        )}
        {connections?.length === 0 && (
          <p className="text-small text-muted-foreground">Add a connection first.</p>
        )}
      </section>
    </div>
  );
}

// ── connections ──────────────────────────────────────────────

function ConnectionsSection({
  assistantId,
  connections,
  usedBy,
  onChanged,
}: {
  assistantId: string;
  connections: HttpConnection[] | null;
  usedBy: (id: string) => string[];
  onChanged: () => Promise<void>;
}) {
  const [editing, setEditing] = useState<HttpConnection | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const remove = async (c: HttpConnection) => {
    const users = usedBy(c.id);
    if (users.length) {
      setError(`'${users[0]}' calls ${c.name}. Remove or change that call first.`);
      return;
    }
    setError(null);
    try {
      await httpConnections.remove(assistantId, c.id);
      await onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't remove it.");
    }
  };

  return (
    <section className="flex flex-col gap-3" aria-label="Connections">
      <div>
        <p className="font-medium">Connections</p>
        <p className="text-small text-muted-foreground">
          The company systems this assistant calls. Credentials are stored sealed and never shown
          again. Systems on the office network need an internal destination, which the owner adds
          under Organisation settings.
        </p>
      </div>
      {connections === null ? (
        <p className="text-small text-muted-foreground">Loading…</p>
      ) : (
        connections.length > 0 && (
          <ul className="border-border border-t">
            {connections.map((c) => (
              <li key={c.id} className="border-border flex items-center gap-2 border-b py-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate">{c.name}</p>
                  <p className="text-small text-muted-foreground truncate font-mono">
                    {c.base_url}
                  </p>
                </div>
                {c.send_asker && <Badge variant="muted">Says who asks</Badge>}
                <Button size="sm" variant="ghost" onClick={() => setEditing(c)}>
                  Edit
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove ${c.name}`}
                  onClick={() => void remove(c)}
                >
                  <Trash2 aria-hidden className="size-4" />
                </Button>
              </li>
            ))}
          </ul>
        )
      )}
      {error && <Alert role="alert">{error}</Alert>}
      {editing ? (
        <ConnectionForm
          assistantId={assistantId}
          existing={editing === "new" ? null : editing}
          onCancel={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await onChanged();
          }}
        />
      ) : (
        <Button
          size="sm"
          variant="outline"
          className="self-start"
          onClick={() => setEditing("new")}
        >
          <Plus aria-hidden className="size-4" />
          Add a connection
        </Button>
      )}
    </section>
  );
}

type HeaderRow = { name: string; value: string };

function ConnectionForm({
  assistantId,
  existing,
  onCancel,
  onSaved,
}: {
  assistantId: string;
  existing: HttpConnection | null;
  onCancel: () => void;
  onSaved: () => Promise<void>;
}) {
  const id = useId();
  const [name, setName] = useState(existing?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(existing?.base_url ?? "https://");
  const [sendAsker, setSendAsker] = useState(existing?.send_asker ?? true);
  // New headers replace the stored ones; untouched, the stored ones stay.
  const [replacing, setReplacing] = useState(existing === null);
  const [rows, setRows] = useState<HeaderRow[]>(
    existing === null ? [{ name: "Authorization", value: "" }] : [],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const headers = Object.fromEntries(
      rows.filter((r) => r.name.trim()).map((r) => [r.name.trim(), r.value]),
    );
    try {
      if (existing === null) {
        await httpConnections.create(assistantId, {
          name,
          base_url: baseUrl,
          headers,
          send_asker: sendAsker,
        });
      } else {
        await httpConnections.update(assistantId, existing.id, {
          name,
          base_url: baseUrl,
          send_asker: sendAsker,
          ...(replacing ? { headers } : {}),
        });
      }
      await onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save it.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={save} className="border-border flex flex-col gap-3 rounded-lg border p-3">
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-name`}>Name</Label>
        <Input
          id={`${id}-name`}
          required
          maxLength={80}
          placeholder="IT tickets"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-url`}>Address</Label>
        <Input
          id={`${id}-url`}
          required
          type="url"
          className="font-mono"
          placeholder="https://tickets.corp.example.com/api"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
        />
        <p className="text-small text-muted-foreground">
          Each call&apos;s path is added to this. No credentials or query here.
        </p>
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1">Headers</legend>
        {existing !== null && !replacing ? (
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-small text-muted-foreground">
              {existing.header_names.length
                ? `Stored: ${existing.header_names.join(", ")}. The values aren't shown.`
                : "No headers are stored."}
            </p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => {
                setReplacing(true);
                setRows(existing.header_names.map((n) => ({ name: n, value: "" })));
              }}
            >
              Replace headers
            </Button>
          </div>
        ) : (
          <>
            {rows.map((r, i) => (
              <div key={i} className="flex flex-wrap items-end gap-2">
                <div className="flex min-w-[8rem] flex-1 flex-col gap-1">
                  <Label htmlFor={`${id}-h${i}-name`}>Header</Label>
                  <Input
                    id={`${id}-h${i}-name`}
                    className="font-mono"
                    pattern="[A-Za-z0-9_-]+"
                    value={r.name}
                    onChange={(e) =>
                      setRows(rows.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))
                    }
                  />
                </div>
                <div className="flex min-w-[10rem] flex-[2] flex-col gap-1">
                  <Label htmlFor={`${id}-h${i}-value`}>Value</Label>
                  <Input
                    id={`${id}-h${i}-value`}
                    type="password"
                    autoComplete="off"
                    className="font-mono"
                    value={r.value}
                    onChange={(e) =>
                      setRows(rows.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))
                    }
                  />
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove ${r.name || "this header"}`}
                  onClick={() => setRows(rows.filter((_, j) => j !== i))}
                >
                  <Trash2 aria-hidden className="size-4" />
                </Button>
              </div>
            ))}
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="self-start"
              onClick={() => setRows([...rows, { name: "", value: "" }])}
            >
              <Plus aria-hidden className="size-4" />
              Add a header
            </Button>
          </>
        )}
      </fieldset>

      <Toggle
        label="Say who is asking"
        checked={sendAsker}
        onChange={setSendAsker}
        hint="Sends X-Asker-Email, X-Asker-Name and X-Asker-Id to this system only, so it can answer as that person. Calls the asker confirms need it."
      />

      {error && <Alert role="alert">{error}</Alert>}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy}>
          {busy ? "Saving…" : "Save connection"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

// ── calls ────────────────────────────────────────────────────

function CallForm({
  assistantId,
  connections,
  initial,
  taken,
  onCancel,
  onSave,
}: {
  assistantId: string;
  connections: HttpConnection[];
  initial: HttpAction;
  taken: string[];
  onCancel: () => void;
  onSave: (action: HttpAction) => void;
}) {
  const id = useId();
  const [draft, setDraft] = useState<HttpAction>(initial);
  const [nameTouched, setNameTouched] = useState(Boolean(initial.name));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (patch: Partial<HttpAction>) => setDraft((d) => ({ ...d, ...patch }));
  const method = (draft.method ?? "POST") as Method;
  const conn = connections.find((c) => c.id === draft.connection_id);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (taken.includes(draft.name)) {
      setError(`Another action is already called ${draft.name}.`);
      return;
    }
    setBusy(true);
    try {
      const out = await approvals.checkHttpAction(assistantId, draft);
      if (!out.ok) {
        setError(out.error ?? "This call can't be saved.");
        return;
      }
      onSave(draft);
    } catch (err) {
      setError(`Couldn't check it. ${err instanceof ApiError ? err.message : "Try again."}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={save} className="border-border flex flex-col gap-3 rounded-lg border p-3">
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-title`}>What people read</Label>
        <Input
          id={`${id}-title`}
          required
          maxLength={80}
          placeholder="Open an IT ticket"
          value={draft.title}
          onChange={(e) =>
            set({
              title: e.target.value,
              ...(nameTouched ? {} : { name: actionName(e.target.value) }),
            })
          }
        />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-name`}>Name</Label>
        <Input
          id={`${id}-name`}
          required
          maxLength={40}
          pattern="[a-z][a-z0-9_]*"
          className="font-mono"
          value={draft.name}
          onChange={(e) => {
            setNameTouched(true);
            set({ name: e.target.value });
          }}
        />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-desc`}>When to use it</Label>
        <Textarea
          id={`${id}-desc`}
          maxLength={1000}
          placeholder="When someone reports something broken: their laptop, VPN, an app."
          value={draft.description ?? ""}
          onChange={(e) => set({ description: e.target.value })}
        />
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex min-w-[10rem] flex-1 flex-col gap-1">
          <Label htmlFor={`${id}-conn`}>Connection</Label>
          <Select
            id={`${id}-conn`}
            required
            value={draft.connection_id}
            onChange={(e) => set({ connection_id: e.target.value })}
          >
            {connections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={`${id}-method`}>Method</Label>
          <Select
            id={`${id}-method`}
            value={method}
            onChange={(e) => {
              const m = e.target.value as Method;
              // A GET only reads; anything else changes something.
              const tier = m === "GET" ? "read" : draft.tier === "read" ? "approver" : draft.tier;
              set({ method: m, tier });
            }}
          >
            {METHODS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </Select>
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-path`}>Path</Label>
        <Input
          id={`${id}-path`}
          required
          maxLength={500}
          className="font-mono"
          placeholder="/tickets/{ticket_id}"
          value={draft.path}
          onChange={(e) => set({ path: e.target.value })}
        />
        <p className="text-small text-muted-foreground">
          {conn ? <span className="font-mono">{conn.base_url}</span> : null}
          {conn ? " + this. " : ""}
          Values not in the path go in the{" "}
          {method === "GET" || method === "DELETE" ? "query string" : "JSON body"}.
        </p>
      </div>

      <ParamsFieldset
        id={id}
        params={draft.params ?? []}
        slotLabel="Value"
        onChange={(params) => set({ params })}
      />

      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-tier`}>How it runs</Label>
        <Select
          id={`${id}-tier`}
          value={draft.tier ?? "approver"}
          disabled={method === "GET"}
          onChange={(e) => set({ tier: e.target.value as Tier })}
        >
          {method === "GET" ? (
            <option value="read">At once (reads only)</option>
          ) : (
            <>
              <option value="confirm">The person asking confirms</option>
              <option value="approver">An approver decides</option>
            </>
          )}
        </Select>
        <p className="text-small text-muted-foreground">
          {method === "GET"
            ? "A GET only reads, so it runs without asking anyone."
            : draft.tier === "confirm"
              ? "For requests about the person themselves. The connection has to say who is asking."
              : "Waits for one of the assistant's approvers, even hours later. Nobody decides their own."}
          {draft.tier === "confirm" && conn && !conn.send_asker
            ? ` ${conn.name} doesn't say who is asking yet.`
            : ""}
        </p>
      </div>

      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-fields`}>Fields the assistant reads (comma-separated)</Label>
        <Input
          id={`${id}-fields`}
          className="font-mono"
          placeholder="id, status, data.assignee"
          value={(draft.fields ?? []).join(", ")}
          onChange={(e) =>
            set({
              fields: e.target.value
                .split(",")
                .map((f) => f.trim())
                .filter(Boolean),
            })
          }
        />
        <p className="text-small text-muted-foreground">
          Leave empty for the whole answer. Naming fields keeps what else the system returns away
          from the assistant.
        </p>
      </div>

      {error && <Alert role="alert">{error}</Alert>}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy}>
          {busy ? "Checking…" : "Save call"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
