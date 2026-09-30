"use client";

import { ChevronRight } from "lucide-react";
import { useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { LineBullet } from "@/components/ui/line-bullet";
import { SectionHeading } from "@/components/ui/section-heading";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, type PipelineSuggestion } from "@/lib/api";
import { useRealModel } from "@/lib/instance";
import { describeSource } from "@/lib/prompt-assist";

const MIN_DESCRIPTION = 10;

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** The node type a suggested capability becomes, for its line bullet. Keys
 *  come from the server: `knowledge_base`, `database:<id>`, `mcp:<id>`,
 *  `subagent:<role>`, `memory_tool`, and the built-in tool names. */
function capabilityType(key: string): string {
  const kind = key.split(":")[0];
  if (kind === "knowledge_base") return "knowledge_base";
  if (kind === "database") return "database";
  if (kind === "mcp") return "mcp_server";
  if (kind === "subagent") return "subagent";
  if (kind === "memory_tool") return "memory";
  return "tool";
}

/** Describe the assistant, get a whole starter pipeline back, look it over,
 *  then apply it or not. Where it comes from and what applying does are the
 *  caller's: the build page saves it over the draft, the guided setup
 *  carries it into the next steps. */
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
      setError(
        err instanceof ApiError
          ? `${err.message} Change the description or try again.`
          : "Couldn't suggest a pipeline. Check your connection, then try again.",
      );
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
      <div className="flex flex-col gap-6 text-sm">
        <section aria-label="Recommended pipeline" className="flex flex-col gap-3">
          <SectionHeading level={4} title="What it would use" />
          {suggestion.capabilities.length === 0 ? (
            <p className="text-muted-foreground max-w-[60ch]">
              Only conversation: nothing in the description calls for a knowledge base, a database
              or tools. You can add any of them on the canvas later.
            </p>
          ) : (
            <ul className="border-border divide-border flex flex-col divide-y border-y">
              {suggestion.capabilities.map((c) => (
                <li key={c.key} className="flex items-start gap-3 py-2.5">
                  <LineBullet type={capabilityType(c.key)} size={20} decorative />
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <span className="font-medium break-words">{c.label}</span>
                    {c.why && (
                      <span className="text-muted-foreground text-small max-w-[60ch]">{c.why}</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {suggestion.changes.length > 0 && (
          <section aria-labelledby={`${ids}-changes`} className="flex flex-col gap-2">
            <SectionHeading level={4} id={`${ids}-changes`} title="What applying it changes" />
            <ul className="text-muted-foreground flex max-w-[60ch] list-disc flex-col gap-1 pl-5">
              {suggestion.changes.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </section>
        )}

        <details className="group border-border rounded-md border">
          <summary className="focus-visible:ring-ring flex cursor-pointer list-none items-center gap-2 rounded-md px-3 py-2 font-medium focus-visible:ring-2 focus-visible:outline-none [&::-webkit-details-marker]:hidden">
            <ChevronRight
              aria-hidden
              className="text-muted-foreground size-4 shrink-0 transition-transform duration-120 group-open:rotate-90"
            />
            System prompt and {plural(rules.length, "rule", "rules")}
          </summary>
          <div className="border-border flex flex-col gap-3 border-t px-3 py-3">
            <p className="max-w-[68ch] whitespace-pre-wrap">{suggestion.config.system_prompt}</p>
            {rules.length > 0 && (
              <ul className="flex max-w-[68ch] list-disc flex-col gap-1 pl-5">
                {rules.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            )}
          </div>
        </details>

        {issues > 0 && (
          <p className="text-muted-foreground text-small max-w-[60ch]">
            The canvas will show {plural(issues, "thing", "things")} to finish, such as a data
            source to add.
          </p>
        )}
        <p className="text-muted-foreground text-small" data-testid="pipeline-source">
          {describeSource(suggestion, "Suggested")}
        </p>
        <div className="flex flex-wrap gap-2">
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
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-description`}>What is this assistant for?</Label>
        <Textarea
          id={`${ids}-description`}
          aria-describedby={`${ids}-description-hint`}
          rows={4}
          maxLength={4000}
          placeholder="For example: Answers customers' questions about orders and returns from our help-centre articles and the orders database. Friendly and brief."
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <p id={`${ids}-description-hint`} className="text-muted-foreground text-small max-w-[60ch]">
          Who it serves, what it answers from, and what it should be able to do. You get a pipeline
          to look over before anything changes.
          {tooShort && " Write at least 10 characters."}
        </p>
      </div>
      {realModel !== null && (
        <p className="text-muted-foreground text-small max-w-[60ch]">
          {realModel
            ? "Uses the assistant's main model. The cost, usually a few cents, goes on its usage."
            : "Free on this server: it is chosen from the words in the description."}
        </p>
      )}
      {error && <Alert>{error}</Alert>}
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" disabled={busy || tooShort} onClick={ask}>
          {busy ? "Suggesting…" : "Suggest a pipeline"}
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
