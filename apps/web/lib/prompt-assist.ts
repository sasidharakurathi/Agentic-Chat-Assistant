import type { PromptSuggestion } from "@/lib/api";

/** Rules as the textarea holds them: one per line, blanks dropped. */
export function parseRules(text: string): string[] {
  return text
    .split("\n")
    .map((r) => r.trim())
    .filter(Boolean);
}

const sameRule = (r: string) => r.toLowerCase().replace(/\s+/g, " ").trim().replace(/\.$/, "");

/** The rules the builder already has, then the suggested ones they don't:
 *  accepting a suggestion never drops a rule someone wrote by hand. */
export function mergeRules(existing: string[], suggested: string[]): string[] {
  const out = [...existing];
  const seen = new Set(existing.map(sameRule));
  for (const rule of suggested) {
    const key = sameRule(rule);
    if (key && !seen.has(key)) {
      seen.add(key);
      out.push(rule.trim());
    }
  }
  return out;
}

/** Who wrote the draft and what it cost, in a line. */
export function sourceNote(
  s: Pick<PromptSuggestion, "source" | "model" | "cost_usd">,
  verb = "Written",
): string {
  if (s.source === "template") {
    return `${verb} from a template: this instance doesn't call a model for this, so it was free.`;
  }
  const cost = s.cost_usd < 0.01 ? "under $0.01" : `$${s.cost_usd.toFixed(2)}`;
  return `${verb} by ${s.model ?? "the model"} · ${cost}, on this assistant's usage.`;
}
