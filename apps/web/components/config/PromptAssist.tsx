"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, assist, type PromptSuggestion } from "@/lib/api";
import { useRealModel } from "@/lib/instance";
import { parseRules, sourceNote } from "@/lib/prompt-assist";

export type PromptAccept = { systemPrompt: string; rules: string[] };

const DEFAULT_PROMPT = "You are a helpful assistant.";
const MIN_DESCRIPTION = 10;

/** "Write it for me" under the system prompt (task 5.5): describe the
 *  assistant, get a draft prompt and rules to edit, then use it or not.
 *  Nothing is saved until "Use this", which goes through the normal draft
 *  save like any other edit. */
export function PromptAssist({
  assistantId,
  currentPrompt,
  model,
  canApplyRules,
  onAccept,
}: {
  assistantId: string;
  currentPrompt: string;
  /** The main model, which writes the draft when the instance calls one. */
  model: string;
  /** False when there is no Guardrails node for the rules to go into. */
  canApplyRules: boolean;
  onAccept: (accepted: PromptAccept) => void;
}) {
  const ids = useId();
  const realModel = useRealModel();
  const hasPrompt = currentPrompt.trim() !== "" && currentPrompt.trim() !== DEFAULT_PROMPT;
  const [open, setOpen] = useState(false);
  const [description, setDescription] = useState("");
  const [improve, setImprove] = useState(hasPrompt);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<PromptSuggestion | null>(null);
  const [prompt, setPrompt] = useState("");
  const [rules, setRules] = useState("");

  const close = () => {
    setOpen(false);
    setDraft(null);
    setError(null);
  };

  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      const s = await assist.generatePrompt(assistantId, {
        description: description.trim(),
        current_prompt: improve && hasPrompt ? currentPrompt : null,
      });
      setDraft(s);
      setPrompt(s.system_prompt);
      setRules(s.rules.join("\n"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not write a draft");
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <div className="flex items-center gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>
          Write it for me
        </Button>
        <span className="text-muted-foreground text-xs">
          Describe the assistant; get a draft prompt and rules to edit.
        </span>
      </div>
    );
  }

  if (draft) {
    return (
      <div className="border-border flex flex-col gap-3 rounded-md border p-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${ids}-prompt`}>Suggested system prompt</Label>
          <Textarea
            id={`${ids}-prompt`}
            rows={10}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
          <p className="text-muted-foreground text-xs">Replaces the current system prompt.</p>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${ids}-rules`}>Suggested rules</Label>
          <Textarea
            id={`${ids}-rules`}
            rows={4}
            value={rules}
            onChange={(e) => setRules(e.target.value)}
          />
          <p className="text-muted-foreground text-xs">
            {canApplyRules
              ? "One per line. Added to your Guardrails rules; ones you already have are skipped."
              : "There is no Guardrails node, so only the prompt will be used. Add one to use rules."}
          </p>
        </div>
        <p className="text-muted-foreground text-xs" data-testid="prompt-assist-source">
          {sourceNote(draft)}
        </p>
        <div className="flex gap-2">
          <Button
            type="button"
            size="sm"
            disabled={!prompt.trim()}
            onClick={() => {
              onAccept({
                systemPrompt: prompt.trim(),
                rules: canApplyRules ? parseRules(rules) : [],
              });
              close();
            }}
          >
            Use this
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => setDraft(null)}>
            Edit description
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={close}>
            Discard
          </Button>
        </div>
      </div>
    );
  }

  const tooShort = description.trim().length < MIN_DESCRIPTION;
  return (
    <div className="border-border flex flex-col gap-3 rounded-md border p-3">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-description`}>What is this assistant for?</Label>
        <Textarea
          id={`${ids}-description`}
          rows={3}
          maxLength={4000}
          placeholder="e.g. Answers customers' questions about orders, returns and delivery for our online shop. Friendly and brief; hands anything about payments to a person."
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <p className="text-muted-foreground text-xs">
          Who it serves, what it helps with, its tone, and what it must not do. It already knows
          what this assistant is connected to.
        </p>
      </div>
      {hasPrompt && (
        <label className="flex items-center justify-between gap-3 text-sm">
          <span>Improve the current prompt instead of starting over</span>
          <Switch checked={improve} onCheckedChange={setImprove} />
        </label>
      )}
      {realModel !== null && (
        <p className="text-muted-foreground text-xs">
          {realModel
            ? `Uses ${model}; the cost (usually a few cents) goes on this assistant's usage.`
            : "Free on this instance: a template writes the draft."}
        </p>
      )}
      {error && (
        <p className="text-destructive text-xs" role="alert">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="button" size="sm" disabled={busy || tooShort} onClick={generate}>
          {busy ? "Writing…" : "Write a draft"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={close} disabled={busy}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
