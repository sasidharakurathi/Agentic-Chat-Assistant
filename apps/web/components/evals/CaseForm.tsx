"use client";

import { useId, useState, type FormEvent } from "react";

import { Toggle } from "@/components/config/panels";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { EvalCase, EvalCaseDraft } from "@/lib/api";

const lines = (text: string) =>
  text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);

const commas = (text: string) =>
  text
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);

/** One eval case (task 6.1): the question, and what a good answer looks
 *  like. Only the question is required; everything else is a check the
 *  answer has to meet, and a case with none passes by answering at all. */
export function CaseForm({
  open,
  editing,
  busy,
  onClose,
  onSave,
}: {
  open: boolean;
  /** The case being changed, or null for a new one. */
  editing: EvalCase | null;
  busy: boolean;
  onClose: () => void;
  onSave: (draft: EvalCaseDraft) => void;
}) {
  const formId = useId();
  const e = editing?.expected;
  const [input, setInput] = useState(editing?.input ?? "");
  const [reference, setReference] = useState(e?.reference ?? "");
  const [contains, setContains] = useState((e?.contains ?? []).join("\n"));
  const [notContains, setNotContains] = useState((e?.not_contains ?? []).join("\n"));
  const [tools, setTools] = useState((e?.tools ?? []).join(", "));
  const [cites, setCites] = useState(e?.cites ?? false);
  const [refuses, setRefuses] = useState(e?.refuses ?? false);
  const [sources, setSources] = useState((editing?.labels.relevant_sources ?? []).join("\n"));

  function submit(ev: FormEvent) {
    ev.preventDefault();
    if (!input.trim()) return;
    onSave({
      input: input.trim(),
      expected: {
        reference: reference.trim() || null,
        contains: lines(contains),
        not_contains: lines(notContains),
        tools: commas(tools),
        cites,
        refuses,
      },
      labels: {
        relevant_sources: lines(sources),
        // Kept as they were: chunk ids come from an import, not this form.
        relevant_chunk_ids: editing?.labels.relevant_chunk_ids ?? [],
      },
    });
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      wide
      dismissOnBackdrop={false}
      title={editing ? "Change this case" : "Add a case"}
      description="A question to ask the assistant, and what a good answer looks like."
      footer={
        <>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form={formId} disabled={busy || !input.trim()}>
            {busy ? "Saving…" : editing ? "Save case" : "Add case"}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="flex flex-col gap-4">
        <Field id={`${formId}-input`} label="Question">
          <Textarea
            id={`${formId}-input`}
            required
            rows={2}
            value={input}
            placeholder="What is the refund window?"
            onChange={(ev) => setInput(ev.target.value)}
          />
        </Field>
        <Field
          id={`${formId}-ref`}
          label="Reference answer"
          hint="Optional. The judge compares the answer with this."
        >
          <Textarea
            id={`${formId}-ref`}
            rows={2}
            value={reference}
            onChange={(ev) => setReference(ev.target.value)}
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            id={`${formId}-says`}
            label="The answer must say"
            hint="One phrase per line. Capitals and spacing don't matter."
          >
            <Textarea
              id={`${formId}-says`}
              rows={3}
              value={contains}
              placeholder="30 days"
              onChange={(ev) => setContains(ev.target.value)}
            />
          </Field>
          <Field id={`${formId}-not`} label="The answer must not say" hint="One phrase per line.">
            <Textarea
              id={`${formId}-not`}
              rows={3}
              value={notContains}
              onChange={(ev) => setNotContains(ev.target.value)}
            />
          </Field>
        </div>
        <Field
          id={`${formId}-tools`}
          label="It must use these tools"
          hint="Short names, separated by commas: kb_search, sql_query, calculator."
        >
          <Input
            id={`${formId}-tools`}
            value={tools}
            onChange={(ev) => setTools(ev.target.value)}
          />
        </Field>
        <Field
          id={`${formId}-sources`}
          label="Sources it should find"
          hint="One source name per line, as it appears in the Sources tab. Used to score retrieval."
        >
          <Textarea
            id={`${formId}-sources`}
            rows={2}
            value={sources}
            placeholder="Refund policy"
            onChange={(ev) => setSources(ev.target.value)}
          />
        </Field>
        <div className="border-border flex flex-col gap-1 border-t pt-3">
          <Toggle label="The answer must cite a source" checked={cites} onChange={setCites} />
          <Toggle
            label="The assistant should decline this"
            hint="For requests it must refuse. The judge marks an answer down if it goes along."
            checked={refuses}
            onChange={setRefuses}
          />
        </div>
      </form>
    </Dialog>
  );
}

function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-small text-muted-foreground max-w-[60ch]">{hint}</p>}
    </div>
  );
}
