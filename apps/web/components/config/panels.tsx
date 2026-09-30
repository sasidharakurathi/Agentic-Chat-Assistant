"use client";

import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";

import { Alert } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Lamp } from "@/components/ui/lamp";
import { SectionHeading } from "@/components/ui/section-heading";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import type { AssistantConfig, DbPermissions, EffortLevel } from "@/lib/api";
import { useOfflineMode } from "@/lib/instance";
import { carryShared, modelName, pickModel, selectedModel, setTurns } from "@/lib/subagent-models";
import { cn } from "@/lib/utils";

type Data = Record<string, unknown>;
const s = (v: unknown, d = "") => (v == null ? d : String(v));
const b = (v: unknown) => v === true;
const joinIds = (...ids: (string | undefined)[]) => ids.filter(Boolean).join(" ") || undefined;

export { modelName };

function ModelOptions({ models }: { models: string[] }) {
  return (
    <>
      {models.map((m) => (
        <option key={m} value={m}>
          {modelName(m)}
        </option>
      ))}
    </>
  );
}

/** A labelled form field: label, the control, and a hint below it. The label
 *  and hint are tied to the control (`htmlFor` and `aria-describedby`) when
 *  the child is a single element that takes an `id`, as Input, Select and
 *  Textarea do. */
export function Field({
  label,
  hint,
  children,
  id,
  className,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
  /** The control's id; one is made up when it has none. */
  id?: string;
  className?: string;
}) {
  const auto = useId();
  const single = isValidElement(children)
    ? (children as ReactElement<{ id?: string; "aria-describedby"?: string }>)
    : null;
  const controlId = id ?? single?.props.id ?? `${auto}-control`;
  const hintId = hint ? `${auto}-hint` : undefined;
  const control = single
    ? cloneElement(single, {
        id: controlId,
        "aria-describedby": joinIds(single.props["aria-describedby"], hintId),
      })
    : children;
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <Label htmlFor={single ? controlId : undefined}>{label}</Label>
      {control}
      {hint && (
        <p id={hintId} className="text-muted-foreground text-small max-w-[60ch]">
          {hint}
        </p>
      )}
    </div>
  );
}

/** The one toggle row: label and hint on the left, the switch on the right,
 *  and the whole row clickable. A `tone` puts a lamp before the hint, for a
 *  hint that is a warning or a problem rather than an explanation. */
export function Toggle({
  label,
  checked,
  onChange,
  disabled,
  hint,
  tone,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  hint?: ReactNode;
  tone?: "warning" | "destructive";
}) {
  const id = useId();
  const labelId = `${id}-label`;
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <label
      htmlFor={id}
      className={cn(
        "flex items-start justify-between gap-4 py-1",
        disabled ? "cursor-not-allowed" : "cursor-pointer",
      )}
    >
      <span className="flex min-w-0 flex-col gap-0.5">
        <span
          id={labelId}
          className={cn("text-sm font-medium", disabled && "text-muted-foreground")}
        >
          {label}
        </span>
        {hint && (
          <span
            id={hintId}
            className={cn(
              "text-small flex max-w-[60ch] items-start gap-1.5",
              tone ? "text-foreground" : "text-muted-foreground",
            )}
          >
            {tone && <Lamp tone={tone} className="mt-[5px]" />}
            <span>{hint}</span>
          </span>
        )}
      </span>
      <Switch
        id={id}
        checked={checked}
        onCheckedChange={onChange}
        disabled={disabled}
        aria-labelledby={labelId}
        aria-describedby={hintId}
        className="mt-0.5"
      />
    </label>
  );
}

/** A sub-group inside a panel: an H4 and its fields. */
function Group({
  title,
  description,
  children,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <SectionHeading level={4} id={id} title={title} description={description} />
      {children}
    </section>
  );
}

/** Two fields side by side where the panel is wide enough (the Settings
 *  column), stacked in the narrow canvas drawer. */
function Pair({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("grid gap-4 @md:grid-cols-2", className)}>{children}</div>;
}

/** Settings that only apply while the toggle above them is on. */
function Nested({ children }: { children: ReactNode }) {
  return <div className="border-border ml-1 flex flex-col gap-4 border-l-2 pl-4">{children}</div>;
}

/** Root of every panel: a container, so the pairs above respond to the
 *  panel's own width rather than the window's. */
function Panel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("@container flex flex-col gap-6", className)}>{children}</div>;
}

// Tied to the API's own enum: `satisfies` rejects a value the API does not
// accept, and the check below fails to compile if the API gains one this
// list is missing. It used to be a hand-copied array nothing checked.
const EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const satisfies readonly EffortLevel[];
type Missing = Exclude<EffortLevel, (typeof EFFORTS)[number]>;
const _allEfforts: [Missing] extends [never] ? true : Missing = true;
void _allEfforts;

const EFFORT_LABEL: Record<(typeof EFFORTS)[number], string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Maximum",
};

function EffortOptions() {
  return (
    <>
      {EFFORTS.map((x) => (
        <option key={x} value={x}>
          {EFFORT_LABEL[x]}
        </option>
      ))}
    </>
  );
}

/** A number input that means "no limit" when empty. */
function OptionalNumber({
  value,
  onCommit,
  step,
  min,
  placeholder,
  id,
  "aria-describedby": describedBy,
}: {
  value: number | null | undefined;
  onCommit: (v: number | null) => void;
  step?: string;
  min?: number;
  placeholder: string;
  id?: string;
  "aria-describedby"?: string;
}) {
  return (
    <Input
      id={id}
      aria-describedby={describedBy}
      type="number"
      inputMode="decimal"
      className="num"
      step={step}
      min={min}
      placeholder={placeholder}
      defaultValue={value == null ? "" : String(value)}
      onBlur={(e) => {
        const raw = e.target.value.trim();
        onCommit(raw === "" ? null : Number(raw));
      }}
    />
  );
}

export function AgentPanel({
  data,
  models,
  onChange,
  assist,
}: {
  data: Data;
  models: string[];
  onChange: (patch: Data) => void;
  /** "Write it for me", where the assistant already exists. */
  assist?: ReactNode;
}) {
  const modelsObj = (data.models as Data) ?? {};
  const main = (modelsObj.main as Data) ?? {};
  const setMain = (patch: Data) =>
    onChange({ models: { ...modelsObj, main: { ...main, ...patch } } });
  return (
    <Panel>
      <Group title="Instructions">
        <Field
          label="System prompt"
          hint="Who the assistant is, who it serves and how it answers. It reads this before every message."
        >
          <Textarea
            // Remounts when the saved prompt changes from outside (an accepted
            // draft), which an uncontrolled textarea would otherwise not show.
            key={s(data.system_prompt)}
            rows={7}
            defaultValue={s(data.system_prompt)}
            onBlur={(e) => onChange({ system_prompt: e.target.value })}
          />
        </Field>
        {assist}
      </Group>
      <Group title="Model">
        <Field label="Main model">
          <Select
            value={s(main.model)}
            onChange={(e) =>
              onChange({ models: { ...modelsObj, main: { ...main, model: e.target.value } } })
            }
          >
            <ModelOptions models={models} />
          </Select>
        </Field>
        <Pair>
          <Field label="Effort" hint="How hard the model works on each message.">
            <Select
              value={s(main.effort, "high")}
              onChange={(e) =>
                onChange({ models: { ...modelsObj, main: { ...main, effort: e.target.value } } })
              }
            >
              <EffortOptions />
            </Select>
          </Field>
          {/* These three were in the schema and reachable only through the raw
              config API: nothing in the UI could set a spend cap. */}
          <Field
            label="Thinking"
            hint="Adaptive lets the model think first when a message needs it."
          >
            <Select
              value={s((main.thinking as Data | undefined)?.type, "adaptive")}
              onChange={(e) => setMain({ thinking: { type: e.target.value } })}
            >
              <option value="adaptive">Adaptive</option>
              <option value="disabled">Off</option>
            </Select>
          </Field>
        </Pair>
      </Group>
      <Group title="Limits">
        <Pair>
          <Field label="Model calls per message" hint="Leave empty for no limit.">
            <OptionalNumber
              value={main.max_turns as number | null | undefined}
              min={1}
              placeholder="No limit"
              onCommit={(v) => setMain({ max_turns: v })}
            />
          </Field>
          <Field label="Spend per conversation (USD)" hint="Leave empty for no cap.">
            <OptionalNumber
              value={main.max_budget_usd as number | null | undefined}
              step="0.01"
              min={0}
              placeholder="No cap"
              onCommit={(v) => setMain({ max_budget_usd: v })}
            />
          </Field>
        </Pair>
      </Group>
    </Panel>
  );
}

/** The output node's one setting: whether answers carry numbered citations.
 *  It was stored on the node and ignored by the compiler, and the drawer
 *  said there was nothing to configure. */
export function OutputPanel({ data, onChange }: { data: Data; onChange: (patch: Data) => void }) {
  return (
    <Panel>
      <Toggle
        label="Number the sources in answers"
        checked={data.citations !== false}
        onChange={(v) => onChange({ citations: v })}
        hint="Only matters with a knowledge base wired in, and the knowledge base's own citation setting must be on too."
      />
    </Panel>
  );
}

export function GuardrailsPanel({
  data,
  onChange,
}: {
  data: Data;
  onChange: (patch: Data) => void;
}) {
  const rules = (data.rules as string[]) ?? [];
  return (
    <Panel>
      <Field label="Rules" hint="One rule per line. They are added to the system prompt.">
        <Textarea
          key={rules.join("\n")}
          rows={5}
          defaultValue={rules.join("\n")}
          onBlur={(e) =>
            onChange({
              rules: e.target.value
                .split("\n")
                .map((r) => r.trim())
                .filter(Boolean),
            })
          }
        />
      </Field>
      <Group title="Safety checks">
        <div className="flex flex-col gap-3">
          <Toggle
            label="Redact personal data"
            checked={b(data.pii_redaction)}
            onChange={(v) => onChange({ pii_redaction: v })}
            hint="Takes emails, phone, card and ID numbers out of web searches and run details. Your own databases and allowed systems still get them, since looking people up is their job."
          />
          <Toggle
            label="Guard against prompt injection"
            checked={b(data.injection_scan)}
            onChange={(v) => onChange({ injection_scan: v })}
            hint="Messages that try to override or reveal the instructions are answered under the normal rules. Tool results that read like instructions reach the model marked as data, and nothing shaped like a credential is sent to another system. Each case shows as a note in chat."
          />
          <Toggle
            label="Treat tool results as data, not instructions"
            checked={b(data.untrusted_content_notice)}
            onChange={(v) => onChange({ untrusted_content_notice: v })}
            hint="Tells the model to ignore instructions that appear inside tool results or retrieved documents."
          />
          <Toggle
            label="Switch models when one refuses"
            checked={b(data.refusal_fallback)}
            onChange={(v) => onChange({ refusal_fallback: v })}
            hint="If the main model declines a request or can't be reached, a different model answers instead."
          />
        </div>
      </Group>
    </Panel>
  );
}

/** What the assistant remembers: each setting says what it does, now that
 *  all four are enforced. */
export function MemoryPanel({ data, onChange }: { data: Data; onChange: (patch: Data) => void }) {
  const persist = data.persist_history !== false;
  return (
    <Panel className="gap-3">
      <Toggle
        label="Remember earlier messages"
        checked={persist}
        onChange={(v) => onChange({ persist_history: v })}
        hint={
          persist
            ? "Each message is answered with the conversation so far."
            : "Each message is answered on its own. Earlier ones are still saved, but the model doesn't see them."
        }
      />
      {persist && (
        <Nested>
          <Field
            label="Summarize after (tokens)"
            hint="Once a conversation grows past this, its older part is replaced by a summary and only recent messages are kept word for word. Between 8,000 and 900,000. Summaries are written in the background."
          >
            <Input
              type="number"
              inputMode="numeric"
              className="num"
              min={8000}
              max={900000}
              step={1000}
              defaultValue={s(data.summarize_after_tokens, "120000")}
              onBlur={(e) => {
                const n = Math.min(900000, Math.max(8000, Math.round(Number(e.target.value) || 0)));
                e.target.value = String(n);
                onChange({ summarize_after_tokens: n });
              }}
            />
          </Field>
        </Nested>
      )}
      <Toggle
        label="Name conversations automatically"
        checked={b(data.auto_title)}
        onChange={(v) => onChange({ auto_title: v })}
        hint="The first message names the conversation, unless someone renames it first."
      />
      <Toggle
        label="Keep notes about each person"
        checked={b(data.memory_tool)}
        onChange={(v) => onChange({ memory_tool: v })}
        hint="The assistant keeps notes between conversations: preferences, facts, where work stands. Notes are private to each person, who can see and clear them with /memory in chat."
      />
    </Panel>
  );
}

/** Built-in subagents: which are on, and each one's model. Mirrors
 *  `agent/subagents.py`: a role is only used when what it needs is there,
 *  so each says what it needs when it isn't. */
const SUBAGENT_ROLES = [
  {
    key: "retrieval",
    label: "Retrieval",
    does: "Searches the knowledge base over several queries, drops duplicates, and returns the passages worth citing.",
    defaultTurns: 6,
  },
  {
    key: "sql",
    label: "SQL",
    does: "Explores the database schema, runs one correct query, and returns the rows with the exact statement.",
    defaultTurns: 8,
  },
  {
    key: "research",
    label: "Research",
    does: "Searches the web, and the knowledge base if there is one, and returns findings with their sources.",
    defaultTurns: 8,
  },
] as const;

export function SubagentsPanel({
  config,
  models,
  onChange,
}: {
  config: AssistantConfig;
  models: string[];
  onChange: (next: AssistantConfig) => void;
}) {
  const offline = useOfflineMode();
  const subagents = (config.subagents ?? {}) as Data;
  const own = (subagents.models ?? {}) as Record<string, Data>;
  const roles = (config.models ?? {}) as unknown as Data;
  const shared = (roles.subagent ?? {}) as Data;
  const ragOn = Boolean((config.rag as Data | undefined)?.enabled);
  const dbOn = ((config.databases as unknown[] | undefined) ?? []).length > 0;
  const webOn = Boolean(
    ((config.tools as unknown as Data | undefined)?.web_search as Data | undefined)?.enabled,
  );

  const save = (patch: Data) =>
    onChange({ ...config, subagents: { ...subagents, ...patch } } as AssistantConfig);
  const setShared = (patch: Data) =>
    onChange({
      ...config,
      models: { ...roles, subagent: { ...shared, ...patch } },
      // Roles that only add a turn limit follow the shared model.
      subagents: { ...subagents, models: carryShared(own, shared, patch) },
    } as unknown as AssistantConfig);
  /** A role's own settings; none left means it uses the shared model. */
  const setOwn = (role: string, next: Data | null) => {
    const rest = { ...own };
    delete rest[role];
    save({ models: next ? { ...rest, [role]: next } : rest });
  };

  const missing = (role: string): string | null => {
    if (role === "retrieval" && !ragOn)
      return "Needs a knowledge base. Wire one into the agent or into this subagent on the canvas, or it stays idle.";
    if (role === "sql" && !dbOn)
      return "Needs a database. Wire one into the agent or into this subagent on the canvas, or it stays idle.";
    if (role === "research" && !webOn)
      return "Needs web search. Switch on the Web search tool above, or it stays idle.";
    if (role === "research" && offline)
      return "This server runs offline, so web search is off, and this subagent with it.";
    return null;
  };

  const sharedName = modelName(s(shared.model)) || "not set";

  return (
    <Panel>
      <Group
        title="Shared model"
        description="Used by every subagent that has no model of its own."
      >
        <Pair>
          <Field label="Subagent model">
            <Select value={s(shared.model)} onChange={(e) => setShared({ model: e.target.value })}>
              <ModelOptions models={models} />
            </Select>
          </Field>
          <Field label="Effort">
            <Select
              value={s(shared.effort, "high")}
              onChange={(e) => setShared({ effort: e.target.value })}
            >
              <EffortOptions />
            </Select>
          </Field>
        </Pair>
      </Group>
      <Group title="Subagents to use">
        <div className="flex flex-col gap-3">
          {SUBAGENT_ROLES.map((role) => {
            const on = b(subagents[role.key]);
            const mine = own[role.key];
            const problem = on ? missing(role.key) : null;
            return (
              <div key={role.key} className="flex flex-col gap-3">
                <Toggle
                  label={role.label}
                  checked={on}
                  onChange={(v) => save({ [role.key]: v })}
                  hint={problem ?? role.does}
                  tone={problem ? "warning" : undefined}
                />
                {on && (
                  <Nested>
                    <Pair>
                      <Field label="Model">
                        <Select
                          aria-label={`${role.label} subagent model`}
                          value={selectedModel(mine, shared)}
                          onChange={(e) =>
                            setOwn(role.key, pickModel(mine, shared, e.target.value))
                          }
                        >
                          <option value="">Shared model ({sharedName})</option>
                          <ModelOptions models={models} />
                        </Select>
                      </Field>
                      <Field
                        label="Model calls per job"
                        hint={`Leave empty for the default, ${role.defaultTurns}.`}
                      >
                        <OptionalNumber
                          key={String(mine?.max_turns ?? "")}
                          value={mine?.max_turns as number | null | undefined}
                          min={1}
                          placeholder={String(role.defaultTurns)}
                          onCommit={(v) => {
                            if (v === null && !mine) return;
                            setOwn(role.key, setTurns(mine, shared, v));
                          }}
                        />
                      </Field>
                    </Pair>
                  </Nested>
                )}
              </div>
            );
          })}
        </div>
      </Group>
    </Panel>
  );
}

/** A subagent node's drawer: what it does, its model (its own, or the
 *  shared subagent model) and turn limit, and what it can use. */
export function SubagentNodePanel({
  data,
  models,
  sharedModel,
  wiredIn,
  onChange,
}: {
  data: Data;
  models: string[];
  /** The shared subagent model it follows without its own. */
  sharedModel: string;
  /** The capabilities wired into this node, by label: this subagent's alone. */
  wiredIn: string[];
  onChange: (patch: Data) => void;
}) {
  const role = SUBAGENT_ROLES.find((r) => r.key === data.role);
  const own = (data.model as Data | null | undefined) ?? null;
  return (
    <Panel>
      <p className="text-sm">
        <span className="font-medium">{role?.label ?? s(data.role)} subagent.</span>{" "}
        <span className="text-muted-foreground">{role?.does}</span>
      </p>
      <div className="flex flex-col gap-4">
        <Field label="Model">
          <Select
            aria-label="Subagent model"
            value={s(own?.model)}
            onChange={(e) =>
              onChange({
                model: e.target.value ? { ...(own ?? {}), model: e.target.value } : null,
              })
            }
          >
            <option value="">Shared model ({modelName(sharedModel) || "not set"})</option>
            <ModelOptions models={models} />
          </Select>
        </Field>
        {own && (
          <Field label="Effort">
            <Select
              value={s(own.effort, "high")}
              onChange={(e) => onChange({ model: { ...own, effort: e.target.value } })}
            >
              <EffortOptions />
            </Select>
          </Field>
        )}
        <Field
          label="Model calls per job"
          hint={
            role
              ? `Leave empty for the default, ${role.defaultTurns}.`
              : "Leave empty for the default."
          }
        >
          <OptionalNumber
            key={String(data.max_turns ?? "")}
            value={data.max_turns as number | null | undefined}
            min={1}
            placeholder={String(role?.defaultTurns ?? "")}
            onCommit={(v) => onChange({ max_turns: v })}
          />
        </Field>
      </div>
      <Group
        title="What it can use"
        description="Also, within its job, whatever is wired to the agent. Wire a capability into this node instead of the agent to keep it away from the main agent."
      >
        {wiredIn.length > 0 ? (
          <>
            <ul className="border-border divide-border flex flex-col divide-y border-y text-sm">
              {wiredIn.map((w) => (
                <li key={w} className="py-2">
                  {w}
                </li>
              ))}
            </ul>
            <p className="text-muted-foreground text-small">
              Only this subagent can use these, unless they are also wired to the agent.
            </p>
          </>
        ) : (
          <p className="text-muted-foreground text-sm">Nothing is wired into it directly.</p>
        )}
      </Group>
    </Panel>
  );
}

const ROUTER_EXPLAINER =
  "Before each message, the router sorts it as simple, normal or hard. Simple ones (a greeting, a thank-you) run at low effort, hard ones (several steps, comparing, planning, code) at high effort or more, and the rest at the agent's own. Only the effort changes. Run details show how each message was routed.";

/** The router node's drawer. */
export function RouterNodePanel({
  data,
  models,
  onChange,
}: {
  data: Data;
  models: string[];
  onChange: (patch: Data) => void;
}) {
  const model = (data.model as Data | undefined) ?? {};
  return (
    <Panel>
      <p className="text-muted-foreground max-w-[60ch] text-sm">{ROUTER_EXPLAINER}</p>
      <Field label="Router model" hint="A small, fast model is enough to sort a message.">
        <Select
          value={s(model.model)}
          onChange={(e) => onChange({ model: { ...model, model: e.target.value } })}
        >
          <ModelOptions models={models} />
        </Select>
      </Field>
    </Panel>
  );
}

/** The router in Settings: on or off, and its model. Switching it on places
 *  a router node before the agent. */
export function RouterPanel({
  config,
  models,
  onChange,
}: {
  config: AssistantConfig;
  models: string[];
  onChange: (next: AssistantConfig) => void;
}) {
  const on = b((config.router as Data | undefined)?.enabled);
  const roles = (config.models ?? {}) as unknown as Data;
  const routerModel = (roles.router ?? {}) as Data;
  return (
    <Panel className="gap-3">
      <Toggle
        label="Pick the effort for each message"
        checked={on}
        onChange={(v) =>
          onChange({ ...config, router: { ...(config.router as Data), enabled: v } })
        }
        hint={ROUTER_EXPLAINER}
      />
      {on && (
        <Nested>
          <Field label="Router model" hint="A small, fast model is enough to sort a message.">
            <Select
              value={s(routerModel.model)}
              onChange={(e) =>
                onChange({
                  ...config,
                  models: { ...roles, router: { ...routerModel, model: e.target.value } },
                } as unknown as AssistantConfig)
              }
            >
              <ModelOptions models={models} />
            </Select>
          </Field>
        </Nested>
      )}
    </Panel>
  );
}

export function DatabasePanel({
  data,
  connections,
  onChange,
}: {
  data: Data;
  connections: {
    id: string;
    name: string;
    engine: string;
    write: boolean;
    permissions?: DbPermissions;
  }[];
  onChange: (patch: Data) => void;
}) {
  const current = s(data.connection_id);
  const conn = connections.find((c) => c.id === current);
  const known = Boolean(conn);
  const readOnly = Boolean(conn && !conn.write);
  const exposed = b(data.expose_write);

  return (
    <Panel className="gap-4">
      <Field label="Connection" hint="Add and set up databases in the Databases tab.">
        <Select value={current} onChange={(e) => onChange({ connection_id: e.target.value })}>
          {!known && (
            <option value={current} disabled={!current}>
              {current ? "Removed connection" : "Choose a connection"}
            </option>
          )}
          {connections.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} ({ENGINE_LABEL[c.engine] ?? c.engine})
            </option>
          ))}
        </Select>
      </Field>
      {!known && current && <Alert>Connection removed. Pick another, or remove this node.</Alert>}
      {conn?.permissions && <PermissionSummary engine={conn.engine} p={conn.permissions} />}

      <div className="flex flex-col gap-3">
        <Toggle
          label="Let the agent write SQL for it"
          checked={data.nl2sql !== false}
          onChange={(v) => onChange({ nl2sql: v })}
          hint="The agent writes and runs its own queries against this database."
        />

        {/* Two gates, not one: the connection's own permission says what the
            CREDENTIAL may do; this says what THIS assistant may ask for. On a
            read-only connection the toggle cannot be switched ON: enabling it
            would be a promise the guard refuses to keep, and the server
            reports it as an error that blocks publishing. It can always be
            switched OFF, since that is exactly what clears that error. */}
        <Toggle
          label="Let this assistant change data"
          checked={exposed}
          onChange={(v) => onChange({ expose_write: v })}
          disabled={readOnly && !exposed}
          tone={readOnly && exposed ? "destructive" : undefined}
          hint={
            readOnly
              ? exposed
                ? "This connection is read-only, so these changes can never run. Publishing is blocked until you switch this off, or allow writes on the connection in the Databases tab."
                : "This connection is read-only. Allow writes on the connection in the Databases tab first."
              : "Each change still needs a person's approval in chat, with the exact statement shown."
          }
        />
      </div>
    </Panel>
  );
}

export function JsonView({ value }: { value: unknown }) {
  return (
    <pre
      tabIndex={0}
      className="bg-muted text-code focus-visible:ring-ring max-h-[70vh] overflow-auto rounded-md p-4 font-mono focus-visible:ring-2 focus-visible:outline-none"
    >
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

// ── Knowledge base and data source ───────────────────────────

const n = (v: unknown, d: number) => {
  const x = Number(v);
  return Number.isFinite(x) ? x : d;
};

/** Patch a nested object on the node's data without clobbering its siblings.
 *  `chunking` and `retrieval` are whole sub-objects on the node, and the
 *  backend's `extra="forbid"` means sending a partial one is a 422. */
function nest(data: Data, key: string, patch: Data): Data {
  const current = (data[key] ?? {}) as Data;
  return { [key]: { ...current, ...patch } };
}

/** A number field that commits on blur, for the knowledge base settings. */
function NumberInput({
  value,
  step,
  onCommit,
  id,
  "aria-describedby": describedBy,
}: {
  value: string;
  step?: string;
  onCommit: (raw: string) => void;
  id?: string;
  "aria-describedby"?: string;
}) {
  return (
    <Input
      id={id}
      aria-describedby={describedBy}
      type="number"
      inputMode="decimal"
      className="num"
      step={step}
      defaultValue={value}
      onBlur={(e) => onCommit(e.target.value)}
    />
  );
}

export function KnowledgeBasePanel({
  data,
  onChange,
}: {
  data: Data;
  onChange: (patch: Data) => void;
}) {
  const chunking = (data.chunking ?? {}) as Data;
  const retrieval = (data.retrieval ?? {}) as Data;

  return (
    <Panel>
      <p className="text-muted-foreground max-w-[60ch] text-sm">
        Wiring this into the agent is what switches search on. Wire data sources into it to limit
        what it searches; with none, it searches every indexed source.
      </p>

      <Group title="Models">
        <Field
          label="Embedding model"
          hint="Saved with the assistant. For now the server picks the embedding model it runs, so changing this has no effect yet."
        >
          <Input
            defaultValue={s(data.embedder, "voyage-3-large")}
            onBlur={(e) => onChange({ embedder: e.target.value.trim() })}
          />
        </Field>
        <Field label="Reranking model">
          <Input
            defaultValue={s(data.reranker, "voyage-rerank-2.5")}
            onBlur={(e) => onChange({ reranker: e.target.value.trim() })}
          />
        </Field>
      </Group>

      <Group
        title="Chunking"
        description="Applies the next time a source is indexed. Reindex a source in the Sources tab to apply it to what is already there."
      >
        <Pair>
          <Field label="Tokens per chunk" hint="128 to 4,000.">
            <NumberInput
              value={s(chunking.max_tokens, "800")}
              onCommit={(v) => onChange(nest(data, "chunking", { max_tokens: n(v, 800) }))}
            />
          </Field>
          <Field
            label="Overlap"
            hint="0 to 0.5: the share of a chunk repeated from the one before."
          >
            <NumberInput
              value={s(chunking.overlap, "0.15")}
              step="0.05"
              onCommit={(v) => onChange(nest(data, "chunking", { overlap: n(v, 0.15) }))}
            />
          </Field>
        </Pair>
      </Group>

      <Group title="Retrieval">
        <Toggle
          label="Hybrid search"
          checked={retrieval.hybrid !== false}
          onChange={(v) => onChange(nest(data, "retrieval", { hybrid: v }))}
          hint="Searches by meaning and by keyword, then merges the two."
        />
        <Pair>
          <Field label="Candidates by meaning" hint="1 to 500.">
            <NumberInput
              value={s(retrieval.top_k_dense, "40")}
              onCommit={(v) => onChange(nest(data, "retrieval", { top_k_dense: n(v, 40) }))}
            />
          </Field>
          <Field label="Candidates by keyword" hint="1 to 500.">
            <NumberInput
              value={s(retrieval.top_k_sparse, "40")}
              onCommit={(v) => onChange(nest(data, "retrieval", { top_k_sparse: n(v, 40) }))}
            />
          </Field>
          <Field
            label="Results kept after reranking"
            hint="No more than the candidates above, or the settings are rejected."
          >
            <NumberInput
              value={s(retrieval.rerank_top_n, "8")}
              onCommit={(v) => onChange(nest(data, "retrieval", { rerank_top_n: n(v, 8) }))}
            />
          </Field>
          <Field
            label="Minimum score"
            hint="0 to 1. Results the reranker scores lower are dropped."
          >
            <NumberInput
              value={s(retrieval.min_score, "0.2")}
              step="0.05"
              onCommit={(v) => onChange(nest(data, "retrieval", { min_score: n(v, 0.2) }))}
            />
          </Field>
          <Field
            label="Merge constant"
            hint="How the two searches' rankings are merged. Higher values weigh ranks more evenly. Usually 60."
          >
            <NumberInput
              value={s(retrieval.rrf_k, "60")}
              onCommit={(v) => onChange(nest(data, "retrieval", { rrf_k: n(v, 60) }))}
            />
          </Field>
        </Pair>
      </Group>

      <Group title="Answers">
        <div className="flex flex-col gap-3">
          <Toggle
            label="Cite sources with numbered markers"
            checked={data.citations !== false}
            onChange={(v) => onChange({ citations: v })}
            hint="Answers mark each claim with a number, like [1], that links to its source."
          />
          <Toggle
            label="Add context to each chunk when indexing"
            checked={b(data.contextual_retrieval)}
            onChange={(v) => onChange({ contextual_retrieval: v })}
            hint="A small model writes a line on where each chunk sits in its document, which helps search find it. Costs a little per chunk, and is skipped while the server runs offline."
          />
        </div>
      </Group>
    </Panel>
  );
}

const SOURCE_STATUS: Record<string, string> = {
  pending: "waiting to index",
  processing: "indexing",
  error: "failed",
};

export function DataSourcePanel({
  data,
  sources,
  onChange,
}: {
  data: Data;
  sources: { id: string; name: string; status: string }[];
  onChange: (patch: Data) => void;
}) {
  const current = s(data.data_source_id);
  const known = sources.some((src) => src.id === current);
  return (
    <Panel className="gap-4">
      <Field
        label="Source"
        hint="Wire this into a knowledge base to include it in searches. Add and index sources in the Sources tab."
      >
        <Select value={current} onChange={(e) => onChange({ data_source_id: e.target.value })}>
          {!known && (
            <option value={current} disabled={!current}>
              {current ? "Removed source" : "Choose a source"}
            </option>
          )}
          {sources.map((src) => (
            <option key={src.id} value={src.id}>
              {src.name}
              {src.status !== "ready" ? ` (${SOURCE_STATUS[src.status] ?? src.status})` : ""}
            </option>
          ))}
        </Select>
      </Field>
      {!known && current && (
        <Alert>
          This source was removed from the assistant. Pick another, or remove this node.
        </Alert>
      )}
    </Panel>
  );
}

const ENGINE_LABEL: Record<string, string> = {
  postgres: "PostgreSQL",
  postgresql: "PostgreSQL",
  mysql: "MySQL",
  sqlite: "SQLite",
  mongodb: "MongoDB",
};

/** The connection's permission profile, read-only, next to the node that
 *  uses it. It is edited in one place, the Databases tab, because one
 *  connection can be wired into several assistants and several nodes. */
function PermissionSummary({ engine, p }: { engine: string; p: DbPermissions }) {
  const noun = engine === "mongodb" ? "collections" : "tables";
  const allowed = [p.read && "read", p.write && "write", p.ddl && "change the schema"].filter(
    (x): x is string => Boolean(x),
  );
  const allows = allowed.length
    ? allowed.join(", ").replace(/^./, (c) => c.toUpperCase())
    : "Nothing";
  return (
    <div className="border-border bg-card flex flex-col gap-2 rounded-lg border p-3">
      <h4 className="text-h4 font-semibold">What this connection may do</h4>
      <dl className="text-small grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
        <dt className="text-muted-foreground">Allows</dt>
        <dd>{allows}</dd>
        <dt className="text-muted-foreground">Row limit</dt>
        <dd className="num">{p.row_limit.toLocaleString()}</dd>
        <dt className="text-muted-foreground">Timeout</dt>
        <dd className="num">{(p.statement_timeout_ms / 1000).toFixed(1)} s</dd>
        {p.allow_tables.length > 0 && (
          <>
            <dt className="text-muted-foreground">Only these {noun}</dt>
            <dd className="break-words">{p.allow_tables.join(", ")}</dd>
          </>
        )}
        {p.deny_tables.length > 0 && (
          <>
            <dt className="text-muted-foreground">Hidden {noun}</dt>
            <dd className="break-words">{p.deny_tables.join(", ")}</dd>
          </>
        )}
      </dl>
      <p className="text-muted-foreground text-small">Change these in the Databases tab.</p>
    </div>
  );
}
