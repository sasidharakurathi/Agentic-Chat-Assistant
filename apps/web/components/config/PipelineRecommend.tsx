"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, type PipelineSuggestion } from "@/lib/api";
import { useRealModel } from "@/lib/instance";
import { sourceNote } from "@/lib/prompt-assist";

const MIN_DESCRIPTION = 10;

/** Describe the assistant, get a whole starter pipeline back, look it over,
 *  then apply it or not (task 5.6). Where it comes from and what applying
 *  does are the caller's: the build page saves it over the draft, the
 *  guided setup carries it into the next steps. */
export function PipelineRecommend({
  initialDescription = "",
  request,
  onApply,
  onCancel,
  applyLabel = "Apply",
}: {
  initialDescription?: string;
  request: (description: string) => Promise<PipelineSuggestion>;
  onApply: (suggestion: PipelineSuggestion) => void | Promise<void>;
  onCancel?: () => void;
  applyLabel?: string;
}) {
  const ids = useId();
  const realModel = useRealModel();
  const [description, setDescription] = useState(initialDescription);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [suggestion, setSuggestion] = useState<PipelineSuggestion | null>(null);

  const ask = async () => {
    setBusy(true);
    setError(null);
    try {
      setSuggestion(await request(description.trim()));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not recommend a pipeline");
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!suggestion) return;
    setBusy(true);
    try {
      await onApply(suggestion);
    } finally {
      setBusy(false);
    }
  };

  if (suggestion) {
    const rules = suggestion.config.guardrails.rules;
    const issues = suggestion.validation.errors.length + suggestion.validation.warnings.length;
    return (
      <div className="flex flex-col gap-4 text-sm">
        <section className="flex flex-col gap-2" aria-label="Recommended pipeline">
          <h3 className="text-xs font-semibold tracking-wide uppercase">It would use</h3>
          {suggestion.capabilities.length === 0 ? (
            <p className="text-muted-foreground">
              Just conversation: nothing in the description calls for a knowledge base, a database
              or tools. You can add any of them on the canvas.
            </p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {suggestion.capabilities.map((c) => (
                <li key={c.key}>
                  <span className="font-medium">{c.label}</span>
                  {c.why && <span className="text-muted-foreground"> — {c.why}</span>}
                </li>
              ))}
            </ul>
          )}
        </section>

        {suggestion.changes.length > 0 && (
          <section className="flex flex-col gap-1.5">
            <h3 className="text-xs font-semibold tracking-wide uppercase">Applying it</h3>
            <ul className="text-muted-foreground list-disc pl-5">
              {suggestion.changes.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </section>
        )}

        <details className="border-border rounded-md border px-3 py-2">
          <summary className="cursor-pointer">System prompt and {rules.length} rule(s)</summary>
          <p className="text-muted-foreground mt-2 whitespace-pre-wrap">
            {suggestion.config.system_prompt}
          </p>
          {rules.length > 0 && (
            <ul className="mt-2 list-disc pl-5">
              {rules.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
        </details>

        {issues > 0 && (
          <p className="text-muted-foreground text-xs">
            The canvas will show {issues} thing(s) to finish, such as a data source to add.
          </p>
        )}
        <p className="text-muted-foreground text-xs" data-testid="pipeline-source">
          {sourceNote(suggestion, "Recommended")}
        </p>
        <div className="flex gap-2">
          <Button type="button" size="sm" disabled={busy} onClick={apply}>
            {applyLabel}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => setSuggestion(null)}
          >
            Edit description
          </Button>
          {onCancel && (
            <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
              Discard
            </Button>
          )}
        </div>
      </div>
    );
  }

  const tooShort = description.trim().length < MIN_DESCRIPTION;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-description`}>What is this assistant for?</Label>
        <Textarea
          id={`${ids}-description`}
          rows={4}
          maxLength={4000}
          placeholder="e.g. Answers customers' questions about orders and returns from our help-centre articles and the orders database. Friendly and brief."
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <p className="text-muted-foreground text-xs">
          Who it serves, what it answers from, and what it should be able to do. You get a pipeline
          to look over before anything changes.
        </p>
      </div>
      {realModel !== null && (
        <p className="text-muted-foreground text-xs">
          {realModel
            ? "Uses the assistant's main model; the cost (usually a few cents) goes on its usage."
            : "Free on this instance: chosen from the words in the description."}
        </p>
      )}
      {error && (
        <p className="text-destructive text-xs" role="alert">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="button" size="sm" disabled={busy || tooShort} onClick={ask}>
          {busy ? "Thinking…" : "Recommend a pipeline"}
        </Button>
        {onCancel && (
          <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </div>
  );
}
