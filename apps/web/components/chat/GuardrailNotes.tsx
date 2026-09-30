import type { GuardrailFinding } from "@/lib/api";
import { guardrailTitle } from "@/lib/message-blocks";
import { toolLabel } from "@/lib/tool-label";

/** What the guardrails did during a turn (task 5.3), under its answer. */
export function GuardrailNotes({ items }: { items: GuardrailFinding[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="border-warning bg-warning/10 space-y-1 rounded-md border px-3 py-2 text-xs">
      {items.map((f, i) => (
        <li key={i}>
          <span className="font-medium">🛡 {guardrailTitle(f)}</span>
          {f.tool ? <span className="text-muted-foreground"> · {toolLabel(f.tool)}</span> : null}
          <span className="text-muted-foreground">: {f.detail}</span>
        </li>
      ))}
    </ul>
  );
}
