"use client";

import type { ReactNode } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import type { AssistantConfig, DbPermissions, EffortLevel } from "@/lib/api";
import { cn } from "@/lib/utils";

type Data = Record<string, unknown>;
const s = (v: unknown, d = "") => (v == null ? d : String(v));
const b = (v: unknown) => v === true;

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      {children}
      {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
    </div>
  );
}

export function Toggle({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex items-center justify-between gap-3 py-1 text-sm">
      <span className={disabled ? "text-muted-foreground" : undefined}>{label}</span>
      <Switch checked={checked} onCheckedChange={onChange} disabled={disabled} />
    </label>
  );
}

// Tied to the API's own enum: `satisfies` rejects a value the API does not
// accept, and the check below fails to compile if the API gains one this
// list is missing. It used to be a hand-copied array nothing checked.
const EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const satisfies readonly EffortLevel[];
type Missing = Exclude<EffortLevel, (typeof EFFORTS)[number]>;
const _allEfforts: [Missing] extends [never] ? true : Missing = true;
void _allEfforts;

/** A number input that means "no limit" when empty. */
function OptionalNumber({
  value,
  onCommit,
  step,
  min,
  placeholder,
}: {
  value: number | null | undefined;
  onCommit: (v: number | null) => void;
  step?: string;
  min?: number;
  placeholder: string;
}) {
  return (
    <Input
      type="number"
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
}: {
  data: Data;
  models: string[];
  onChange: (patch: Data) => void;
}) {
  const modelsObj = (data.models as Data) ?? {};
  const main = (modelsObj.main as Data) ?? {};
  const setMain = (patch: Data) =>
    onChange({ models: { ...modelsObj, main: { ...main, ...patch } } });
  return (
    <div className="flex flex-col gap-4">
      <Field label="System prompt">
        <Textarea
          rows={7}
          defaultValue={s(data.system_prompt)}
          onBlur={(e) => onChange({ system_prompt: e.target.value })}
        />
      </Field>
      <Field label="Main model">
        <Select
          value={s(main.model)}
          onChange={(e) =>
            onChange({ models: { ...modelsObj, main: { ...main, model: e.target.value } } })
          }
        >
          {models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Effort" hint="How hard the model works per turn.">
        <Select
          value={s(main.effort, "high")}
          onChange={(e) =>
            onChange({ models: { ...modelsObj, main: { ...main, effort: e.target.value } } })
          }
        >
          {EFFORTS.map((x) => (
            <option key={x}>{x}</option>
          ))}
        </Select>
      </Field>
      {/* These three were in the schema and reachable only through the raw
          config API: nothing in the UI could set a spend cap. */}
      <Field
        label="Extended thinking"
        hint="Adaptive lets the model think before answering when it helps."
      >
        <Select
          value={s((main.thinking as Data | undefined)?.type, "adaptive")}
          onChange={(e) => setMain({ thinking: { type: e.target.value } })}
        >
          <option value="adaptive">adaptive</option>
          <option value="disabled">disabled</option>
        </Select>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Max turns" hint="Model calls per message. Empty: no limit.">
          <OptionalNumber
            value={main.max_turns as number | null | undefined}
            min={1}
            placeholder="no limit"
            onCommit={(v) => setMain({ max_turns: v })}
          />
        </Field>
        <Field label="Budget (USD)" hint="Spend cap per conversation. Empty: none.">
          <OptionalNumber
            value={main.max_budget_usd as number | null | undefined}
            step="0.01"
            min={0}
            placeholder="none"
            onCommit={(v) => setMain({ max_budget_usd: v })}
          />
        </Field>
      </div>
    </div>
  );
}

/** The output node's one setting (task 1.12): whether answers carry
 *  numbered citations. It was stored on the node and ignored by the compiler,
 *  and the drawer said there was nothing to configure. */
export function OutputPanel({ data, onChange }: { data: Data; onChange: (patch: Data) => void }) {
  return (
    <div className="flex flex-col gap-2">
      <Toggle
        label="Number the sources in answers"
        checked={data.citations !== false}
        onChange={(v) => onChange({ citations: v })}
      />
      <p className="text-muted-foreground text-xs">
        Only matters with a knowledge base wired in. Both this and the knowledge base&apos;s own
        setting must be on.
      </p>
    </div>
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
    <div className="flex flex-col gap-3">
      <Field label="Rules" hint="One rule per line. Injected into the system prompt.">
        <Textarea
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
      <Toggle
        label="Redact PII in tool inputs"
        checked={b(data.pii_redaction)}
        onChange={(v) => onChange({ pii_redaction: v })}
      />
      <Toggle
        label="Scan tool inputs for injection"
        checked={b(data.injection_scan)}
        onChange={(v) => onChange({ injection_scan: v })}
      />
      <Toggle
        label="Tell the model retrieved content is untrusted data"
        checked={b(data.untrusted_content_notice)}
        onChange={(v) => onChange({ untrusted_content_notice: v })}
      />
      <Toggle
        label="Refusal fallback"
        checked={b(data.refusal_fallback)}
        onChange={(v) => onChange({ refusal_fallback: v })}
      />
    </div>
  );
}

export function MemoryPanel({ data, onChange }: { data: Data; onChange: (patch: Data) => void }) {
  return (
    <div className="flex flex-col gap-3">
      <Field label="Summarize after N tokens">
        <Input
          type="number"
          defaultValue={s(data.summarize_after_tokens, "120000")}
          onBlur={(e) => onChange({ summarize_after_tokens: Number(e.target.value) })}
        />
      </Field>
      <Toggle
        label="Persist conversation history"
        checked={b(data.persist_history)}
        onChange={(v) => onChange({ persist_history: v })}
      />
      <Toggle
        label="Auto-generate conversation titles"
        checked={b(data.auto_title)}
        onChange={(v) => onChange({ auto_title: v })}
      />
      <Toggle
        label="Enable the memory tool"
        checked={b(data.memory_tool)}
        onChange={(v) => onChange({ memory_tool: v })}
      />
    </div>
  );
}

export function SubagentsPanel({
  config,
  onChange,
}: {
  config: AssistantConfig;
  onChange: (next: AssistantConfig) => void;
}) {
  const subagents = (config.subagents ?? {}) as Data;
  const ragOn = Boolean((config.rag as Data | undefined)?.enabled);
  const set = (key: string, enabled: boolean) =>
    onChange({ ...config, subagents: { ...subagents, [key]: enabled } });

  return (
    <div className="flex flex-col gap-2">
      <p className="text-muted-foreground text-xs">
        A subagent runs a multi-step job in its own context on a cheaper model and hands back only
        its findings — the main agent never sees the dead ends.
      </p>
      <Toggle
        label="Retrieval"
        checked={b(subagents.retrieval)}
        onChange={(v) => set("retrieval", v)}
      />
      <p className="text-muted-foreground text-xs">
        {ragOn
          ? "Searches the knowledge base over several queries, drops duplicates, and returns the passages worth citing."
          : "Needs a knowledge base — wire one into the agent on the canvas first, or this stays inactive."}
      </p>
    </div>
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

  return (
    <div className="flex flex-col gap-3">
      <Field label="Connection" hint="Add and configure databases in the Databases tab.">
        <Select value={current} onChange={(e) => onChange({ connection_id: e.target.value })}>
          {!known && <option value={current}>{current || "(none)"}</option>}
          {connections.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} ({c.engine})
            </option>
          ))}
        </Select>
      </Field>
      {!known && current && (
        <p className="text-destructive text-xs">
          This connection no longer exists. Pick another, or remove this node.
        </p>
      )}
      {conn?.permissions && <PermissionSummary engine={conn.engine} p={conn.permissions} />}

      <Toggle
        label="Let the agent write SQL for it"
        checked={data.nl2sql !== false}
        onChange={(v) => onChange({ nl2sql: v })}
      />

      {/* Two gates, not one: the connection's own permission says what the
          CREDENTIAL may do; this says what THIS assistant may ask for. On a
          read-only connection the toggle cannot be switched ON — enabling it
          would be a promise the guard refuses to keep, and the server now
          reports it as an error that blocks publishing. It can always be
          switched OFF, since that is exactly what clears that error. (It used
          to be merely dimmed, while this comment claimed it was disabled.) */}
      <Toggle
        label="Expose writes to this assistant"
        checked={b(data.expose_write)}
        onChange={(v) => onChange({ expose_write: v })}
        disabled={Boolean(conn && !conn.write && !b(data.expose_write))}
      />
      <p
        className={cn(
          "text-xs",
          conn && !conn.write && b(data.expose_write)
            ? "text-destructive"
            : "text-muted-foreground",
        )}
      >
        {conn && !conn.write
          ? b(data.expose_write)
            ? "This connection is read-only, so exposed writes can never run — publishing is blocked until you turn this off or allow writes on the connection (Databases tab)."
            : "This connection is read-only. Allow writes on the connection first, in the Databases tab."
          : "Writes still require human approval in chat, with the exact statement shown."}
      </p>
    </div>
  );
}

export function JsonView({ value }: { value: unknown }) {
  return (
    <pre className="bg-muted max-h-[70vh] overflow-auto rounded-md p-4 text-xs leading-relaxed">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

// ── Knowledge base / data source (task 2.13) ─────────────────

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
    <div className="flex flex-col gap-4">
      <p className="text-muted-foreground text-xs">
        Wiring this node into the agent is what turns retrieval on. Connect data sources into it to
        limit the search; with none connected it searches every indexed source.
      </p>

      <section className="flex flex-col gap-3">
        <h4 className="text-xs font-semibold tracking-wide uppercase">Models</h4>
        <Field
          label="Embedder"
          hint="Recorded on the config. In v1 the model that actually runs is chosen by RAG_OFFLINE / whether a Voyage key is set, not by this field."
        >
          <Input
            defaultValue={s(data.embedder, "voyage-3-large")}
            onBlur={(e) => onChange({ embedder: e.target.value.trim() })}
          />
        </Field>
        <Field label="Reranker">
          <Input
            defaultValue={s(data.reranker, "voyage-rerank-2.5")}
            onBlur={(e) => onChange({ reranker: e.target.value.trim() })}
          />
        </Field>
      </section>

      <section className="flex flex-col gap-3">
        <h4 className="text-xs font-semibold tracking-wide uppercase">Chunking</h4>
        <p className="text-muted-foreground text-xs">
          Applies to the next indexing run — reindex a source in the Sources tab to apply it to
          content that is already indexed.
        </p>
        <Field label="Max tokens per chunk" hint="128–4000">
          <Input
            type="number"
            defaultValue={s(chunking.max_tokens, "800")}
            onBlur={(e) => onChange(nest(data, "chunking", { max_tokens: n(e.target.value, 800) }))}
          />
        </Field>
        <Field label="Overlap" hint="0–0.5 — fraction of a chunk shared with the previous one">
          <Input
            type="number"
            step="0.05"
            defaultValue={s(chunking.overlap, "0.15")}
            onBlur={(e) => onChange(nest(data, "chunking", { overlap: n(e.target.value, 0.15) }))}
          />
        </Field>
      </section>

      <section className="flex flex-col gap-3">
        <h4 className="text-xs font-semibold tracking-wide uppercase">Retrieval</h4>
        <Toggle
          label="Hybrid search (dense + keyword)"
          checked={retrieval.hybrid !== false}
          onChange={(v) => onChange(nest(data, "retrieval", { hybrid: v }))}
        />
        <Field label="Dense candidates" hint="1–500">
          <Input
            type="number"
            defaultValue={s(retrieval.top_k_dense, "40")}
            onBlur={(e) =>
              onChange(nest(data, "retrieval", { top_k_dense: n(e.target.value, 40) }))
            }
          />
        </Field>
        <Field label="Keyword candidates" hint="1–500">
          <Input
            type="number"
            defaultValue={s(retrieval.top_k_sparse, "40")}
            onBlur={(e) =>
              onChange(nest(data, "retrieval", { top_k_sparse: n(e.target.value, 40) }))
            }
          />
        </Field>
        <Field
          label="Results kept after reranking"
          hint="Must not exceed the candidate pool above, or the config is rejected."
        >
          <Input
            type="number"
            defaultValue={s(retrieval.rerank_top_n, "8")}
            onBlur={(e) =>
              onChange(nest(data, "retrieval", { rerank_top_n: n(e.target.value, 8) }))
            }
          />
        </Field>
        <Field label="Minimum score" hint="0–1, applied to the reranker's score">
          <Input
            type="number"
            step="0.05"
            defaultValue={s(retrieval.min_score, "0.2")}
            onBlur={(e) => onChange(nest(data, "retrieval", { min_score: n(e.target.value, 0.2) }))}
          />
        </Field>
        <Field label="RRF k" hint="Fusion constant; higher flattens the rank weighting">
          <Input
            type="number"
            defaultValue={s(retrieval.rrf_k, "60")}
            onBlur={(e) => onChange(nest(data, "retrieval", { rrf_k: n(e.target.value, 60) }))}
          />
        </Field>
      </section>

      <section className="flex flex-col gap-1">
        <h4 className="text-xs font-semibold tracking-wide uppercase">Answers</h4>
        <Toggle
          label="Cite sources with [n] markers"
          checked={data.citations !== false}
          onChange={(v) => onChange({ citations: v })}
        />
        <Toggle
          label="Contextual retrieval prefix"
          checked={b(data.contextual_retrieval)}
          onChange={(v) => onChange({ contextual_retrieval: v })}
        />
      </section>
    </div>
  );
}

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
    <div className="flex flex-col gap-3">
      <Field label="Source" hint="Add and index sources in the Sources tab.">
        <Select value={current} onChange={(e) => onChange({ data_source_id: e.target.value })}>
          {!known && <option value={current}>{current || "(none)"}</option>}
          {sources.map((src) => (
            <option key={src.id} value={src.id}>
              {src.name}
              {src.status !== "ready" ? ` (${src.status})` : ""}
            </option>
          ))}
        </Select>
      </Field>
      {!known && current && (
        <p className="text-destructive text-xs">
          This source no longer exists on the assistant. Pick another, or delete this node.
        </p>
      )}
      <p className="text-muted-foreground text-xs">
        Connect this node into a knowledge base to include it in searches.
      </p>
    </div>
  );
}

/** The connection's permission profile, read-only, next to the node that
 *  uses it (task 3.11). It is edited in one place, the Databases tab, because
 *  one connection can be wired into several assistants and several nodes. */
function PermissionSummary({ engine, p }: { engine: string; p: DbPermissions }) {
  const noun = engine === "mongodb" ? "collections" : "tables";
  const allowed = [p.read && "read", p.write && "write", p.ddl && "schema changes"].filter(Boolean);
  return (
    <div className="border-border bg-muted/40 rounded-md border px-3 py-2 text-xs">
      <div className="font-medium">What this connection&apos;s credential may do</div>
      <dl className="text-muted-foreground mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        <dt>Allows</dt>
        <dd className="text-foreground">{allowed.length ? allowed.join(", ") : "nothing"}</dd>
        <dt>Row limit</dt>
        <dd className="text-foreground">{p.row_limit}</dd>
        <dt>Timeout</dt>
        <dd className="text-foreground">{(p.statement_timeout_ms / 1000).toFixed(1)} s</dd>
        {p.allow_tables.length > 0 && (
          <>
            <dt>Only {noun}</dt>
            <dd className="text-foreground">{p.allow_tables.join(", ")}</dd>
          </>
        )}
        {p.deny_tables.length > 0 && (
          <>
            <dt>Hidden {noun}</dt>
            <dd className="text-foreground">{p.deny_tables.join(", ")}</dd>
          </>
        )}
      </dl>
      <p className="text-muted-foreground mt-1">Change these in the Databases tab.</p>
    </div>
  );
}
