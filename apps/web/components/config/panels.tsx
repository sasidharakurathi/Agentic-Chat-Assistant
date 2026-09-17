"use client";

import type { ReactNode } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import type { AssistantConfig } from "@/lib/api";

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

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between gap-3 py-1 text-sm">
      <span>{label}</span>
      <Switch checked={checked} onCheckedChange={onChange} />
    </label>
  );
}

const EFFORTS = ["low", "medium", "high", "xhigh", "max"];

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

export function ToolsPanel({
  config,
  onChange,
}: {
  config: AssistantConfig;
  onChange: (next: AssistantConfig) => void;
}) {
  const tools = config.tools;
  const set = (key: string, enabled: boolean) =>
    onChange({ ...config, tools: { ...tools, [key]: { ...tools[key], enabled } } });
  return (
    <div className="flex flex-col gap-2">
      <p className="text-muted-foreground text-xs">
        Enabling a tool adds a wired node to the canvas.
      </p>
      <Toggle
        label="Calculator"
        checked={b(tools.calculator?.enabled)}
        onChange={(v) => set("calculator", v)}
      />
      <Toggle
        label="Date / time"
        checked={b(tools.datetime?.enabled)}
        onChange={(v) => set("datetime", v)}
      />
      <Toggle
        label="Web search"
        checked={b(tools.web_search?.enabled)}
        onChange={(v) => set("web_search", v)}
      />
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
