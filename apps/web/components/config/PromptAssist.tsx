"use client";

import { useId, useState } from "react";

import { Toggle } from "@/components/config/panels";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, assist, type PromptSuggestion } from "@/lib/api";
import { useRealModel } from "@/lib/instance";
import { describeSource, parseRules } from "@/lib/prompt-assist";
import { modelName } from "@/lib/subagent-models";

export type PromptAccept = { systemPrompt: string; rules: string[] };

const DEFAULT_PROMPT = "You are a helpful assistant.";
const MIN_DESCRIPTION = 10;

/** "Write it for me" under the system prompt: describe the assistant, get a
 *  draft prompt and rules to edit, then use it or not. Nothing is saved
 *  until "Use this draft", which goes through the normal draft save like
 *  any other edit. */
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
      setError(
        err instanceof ApiError
          ? `${err.message} Try again, or write the prompt yourself.`
          : "Couldn't write a draft. Check your connection, then try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>
          Write it for me
        </Button>
        <span className="text-muted-foreground text-small">
          Describe the assistant and get a draft prompt and rules to edit.
        </span>
      </div>
    );
  }

  if (draft) {
    return (
      <section
        aria-labelledby={`${ids}-title`}
        className="border-border bg-card flex flex-col gap-4 rounded-lg border p-4"
      >
        <h4 id={`${ids}-title`} className="text-h4 font-semibold">
          Draft to look over
        </h4>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${ids}-prompt`}>Suggested system prompt</Label>
          <Textarea
            id={`${ids}-prompt`}
            aria-describedby={`${ids}-prompt-hint`}
            rows={10}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
          <p id={`${ids}-prompt-hint`} className="text-muted-foreground text-small">
            Replaces the current system prompt.
          </p>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${ids}-rules`}>Suggested rules</Label>
          <Textarea
            id={`${ids}-rules`}
            aria-describedby={`${ids}-rules-hint`}
            rows={4}
            value={rules}
            onChange={(e) => setRules(e.target.value)}
          />
          <p id={`${ids}-rules-hint`} className="text-muted-foreground text-small max-w-[60ch]">
            {canApplyRules
              ? "One per line. Added to your Guardrails rules; ones you already have are skipped."
              : "There is no Guardrails node, so only the prompt will be used. Add one on the canvas to use rules."}
          </p>
        </div>
        <p className="text-muted-foreground text-small" data-testid="prompt-assist-source">
          {describeSource(draft)}
        </p>
        <div className="flex flex-wrap gap-2">
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
            Use this draft
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => setDraft(null)}>
            Edit description
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={close}>
            Discard
          </Button>
        </div>
      </section>
    );
  }

  const tooShort = description.trim().length < MIN_DESCRIPTION;
  return (
    <section
      aria-labelledby={`${ids}-title`}
      className="border-border bg-card flex flex-col gap-4 rounded-lg border p-4"
    >
      <h4 id={`${ids}-title`} className="text-h4 font-semibold">
        Write the prompt for me
      </h4>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-description`}>What is this assistant for?</Label>
        <Textarea
          id={`${ids}-description`}
          aria-describedby={`${ids}-description-hint`}
          rows={3}
          maxLength={4000}
          placeholder="For example: Answers customers' questions about orders, returns and delivery for our online shop. Friendly and brief. Hands anything about payments to a person."
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <p id={`${ids}-description-hint`} className="text-muted-foreground text-small max-w-[60ch]">
          Who it serves, what it helps with, its tone, and what it must not do. It already knows
          what this assistant is connected to.
          {tooShort && " Write at least 10 characters."}
        </p>
      </div>
      {hasPrompt && (
        <Toggle
          label="Improve the current prompt instead of starting over"
          checked={improve}
          onChange={setImprove}
        />
      )}
      {realModel !== null && (
        <p className="text-muted-foreground text-small max-w-[60ch]">
          {realModel
            ? `Uses ${modelName(model)}. The cost, usually a few cents, goes on this assistant's usage.`
            : "Free on this server: a template writes the draft."}
        </p>
      )}
      {error && <Alert>{error}</Alert>}
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" disabled={busy || tooShort} onClick={generate}>
          {busy ? "Writing…" : "Write a draft"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={close} disabled={busy}>
          Cancel
        </Button>
      </div>
    </section>
  );
}
