"use client";

import { Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, approvals, type ActionParam, type DeclaredAction } from "@/lib/api";
import { actionName, TIER_WORDS, type Tier } from "@/lib/actions";

const EMPTY: DeclaredAction = {
  name: "",
  title: "",
  description: "",
  sql: "",
  params: [],
  tier: "approver",
};

/** Named statements on a database (Phase 9.1): the builder writes the SQL
 *  with :slots, the model only fills the parameters, and the platform fills
 *  :asker_email, :asker_name and :asker_id. Each is checked by the server
 *  before it goes on the canvas, and an admin approves it with the version. */
export function ActionsEditor({
  assistantId,
  connectionId,
  actions,
  canWrite,
  onChange,
}: {
  assistantId: string;
  connectionId: string;
  actions: DeclaredAction[];
  /** The connection's own write permission. */
  canWrite: boolean;
  onChange: (next: DeclaredAction[]) => void;
}) {
  const [editing, setEditing] = useState<{ index: number | null; draft: DeclaredAction } | null>(
    null,
  );

  return (
    <section className="flex flex-col gap-3" aria-label="Actions">
      <div>
        <p className="font-medium">Actions</p>
        <p className="text-small text-muted-foreground">
          Named statements the assistant can ask for, with values it fills in. Who asks is added for
          you as :asker_email, :asker_name and :asker_id.
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
                <p className="text-small text-muted-foreground truncate font-mono">{a.name}</p>
              </div>
              <Badge variant={a.tier === "read" ? "muted" : "warning"}>
                {TIER_WORDS[(a.tier ?? "approver") as Tier]}
              </Badge>
              <Button size="sm" variant="ghost" onClick={() => setEditing({ index: i, draft: a })}>
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
        <ActionForm
          assistantId={assistantId}
          connectionId={connectionId}
          initial={editing.draft}
          taken={actions.filter((_, j) => j !== editing.index).map((a) => a.name)}
          canWrite={canWrite}
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
          disabled={!connectionId}
          onClick={() => setEditing({ index: null, draft: { ...EMPTY, params: [] } })}
        >
          <Plus aria-hidden className="size-4" />
          Add an action
        </Button>
      )}
    </section>
  );
}

function ActionForm({
  assistantId,
  connectionId,
  initial,
  taken,
  canWrite,
  onCancel,
  onSave,
}: {
  assistantId: string;
  connectionId: string;
  initial: DeclaredAction;
  taken: string[];
  canWrite: boolean;
  onCancel: () => void;
  onSave: (action: DeclaredAction) => void;
}) {
  const id = useId();
  const [draft, setDraft] = useState<DeclaredAction>(initial);
  const [nameTouched, setNameTouched] = useState(Boolean(initial.name));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const params = draft.params ?? [];
  const set = (patch: Partial<DeclaredAction>) => setDraft((d) => ({ ...d, ...patch }));

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setWarning(null);
    if (taken.includes(draft.name)) {
      setError(`Another action is already called ${draft.name}.`);
      return;
    }
    setBusy(true);
    try {
      const out = await approvals.checkAction(assistantId, connectionId, draft);
      if (!out.ok) {
        setError(out.error ?? "This action can't be saved.");
        return;
      }
      if (out.writes && out.connection_can_write === false && !warning) {
        // Said first; saving again keeps it anyway (writes may be allowed later).
        setWarning(
          "This connection is read-only, so this action could never run, and the assistant can't be submitted with it. Allow writes on the connection in the Databases tab, or save again to keep it for later.",
        );
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
          placeholder="Request access to an app"
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
          placeholder="When someone asks for access to software we license."
          value={draft.description ?? ""}
          onChange={(e) => set({ description: e.target.value })}
        />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-sql`}>SQL</Label>
        <Textarea
          id={`${id}-sql`}
          required
          className="min-h-28 font-mono"
          maxLength={10_000}
          placeholder="INSERT INTO access_requests (app, reason, requester_email) VALUES (:app, :reason, :asker_email)"
          value={draft.sql}
          onChange={(e) => set({ sql: e.target.value })}
        />
      </div>

      <ParamsFieldset id={id} params={params} onChange={(next) => set({ params: next })} />

      <div className="flex flex-col gap-1">
        <Label htmlFor={`${id}-tier`}>How it runs</Label>
        <Select
          id={`${id}-tier`}
          value={draft.tier ?? "approver"}
          onChange={(e) => set({ tier: e.target.value as Tier })}
        >
          <option value="read">At once (reads only)</option>
          <option value="confirm">The person asking confirms (their own rows only)</option>
          <option value="approver">An approver decides</option>
        </Select>
        <p className="text-small text-muted-foreground">
          {draft.tier === "confirm"
            ? "Only for a statement pinned to the person asking: each inserted row carries an :asker_ value, or the WHERE clause has column = :asker_email joined with AND."
            : draft.tier === "read"
              ? "For lookups. Runs without asking anyone."
              : "Waits for one of the assistant's approvers, even hours later. Nobody decides their own."}
          {!canWrite && draft.tier !== "read" ? " This connection is read-only." : ""}
        </p>
      </div>

      {error && <Alert role="alert">{error}</Alert>}
      {warning && <Alert tone="warning">{warning}</Alert>}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy}>
          {busy ? "Checking…" : "Save action"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/** The values an action's model fills in: shared by SQL and HTTP actions
 *  (Phase 9.3), which check them the same way. */
export function ParamsFieldset({
  id,
  params,
  onChange,
  slotLabel = "Slot",
}: {
  id: string;
  params: ActionParam[];
  onChange: (next: ActionParam[]) => void;
  slotLabel?: string;
}) {
  const setParam = (i: number, patch: Partial<ActionParam>) =>
    onChange(params.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1">Values the assistant fills in</legend>
      {params.map((p, i) => (
        <div key={i} className="border-border flex flex-col gap-2 rounded-md border p-2">
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex min-w-[8rem] flex-1 flex-col gap-1">
              <Label htmlFor={`${id}-p${i}-name`}>{slotLabel}</Label>
              <Input
                id={`${id}-p${i}-name`}
                required
                className="font-mono"
                pattern="[a-z][a-z0-9_]*"
                maxLength={40}
                value={p.name}
                onChange={(e) => setParam(i, { name: e.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor={`${id}-p${i}-type`}>Type</Label>
              <Select
                id={`${id}-p${i}-type`}
                value={p.type ?? "string"}
                onChange={(e) => setParam(i, { type: e.target.value as ActionParam["type"] })}
              >
                <option value="string">Text</option>
                <option value="integer">Whole number</option>
                <option value="number">Number</option>
                <option value="boolean">Yes or no</option>
                <option value="date">Date</option>
              </Select>
            </div>
            <label className="flex h-9 items-center gap-2">
              <input
                type="checkbox"
                className="size-4"
                checked={p.required !== false}
                onChange={(e) => setParam(i, { required: e.target.checked })}
              />
              Required
            </label>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              aria-label={`Remove ${p.name || "this value"}`}
              onClick={() => onChange(params.filter((_, j) => j !== i))}
            >
              <Trash2 aria-hidden className="size-4" />
            </Button>
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor={`${id}-p${i}-desc`}>What it is</Label>
            <Input
              id={`${id}-p${i}-desc`}
              maxLength={300}
              value={p.description ?? ""}
              onChange={(e) => setParam(i, { description: e.target.value })}
            />
          </div>
          {(p.type ?? "string") === "string" && (
            <div className="flex flex-col gap-1">
              <Label htmlFor={`${id}-p${i}-choices`}>Only these values (comma-separated)</Label>
              <Input
                id={`${id}-p${i}-choices`}
                value={(p.choices ?? []).join(", ")}
                onChange={(e) =>
                  setParam(i, {
                    choices: e.target.value
                      .split(",")
                      .map((c) => c.trim())
                      .filter(Boolean),
                  })
                }
              />
            </div>
          )}
        </div>
      ))}
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="self-start"
        onClick={() =>
          onChange([
            ...params,
            {
              name: "",
              type: "string",
              description: "",
              required: true,
              max_length: 500,
              choices: [],
            },
          ])
        }
      >
        <Plus aria-hidden className="size-4" />
        Add a value
      </Button>
    </fieldset>
  );
}
