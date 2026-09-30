import { Lamp } from "@/components/ui/lamp";
import { LineBullet } from "@/components/ui/line-bullet";
import type { GuardrailFinding } from "@/lib/api";
import { guardrailTitle } from "@/lib/message-blocks";
import { toolLabel } from "@/lib/tool-label";

/** What the guardrails did during a turn, above its answer: each finding
 *  with the Guardrails bullet, what happened, and where. A finding is
 *  something to check, so it carries the ochre lamp. */
export function GuardrailNotes({ items }: { items: GuardrailFinding[] }) {
  if (items.length === 0) return null;
  return (
    <ul aria-label="Guardrail notes" className="flex flex-col gap-2">
      {items.map((f, i) => (
        <li key={i} className="flex items-start gap-3">
          <LineBullet type="guardrail" size={20} className="mt-px" />
          <div className="min-w-0 flex-1">
            <p className="flex items-center gap-2 font-medium">
              <Lamp tone="warning" />
              {guardrailTitle(f)}
            </p>
            {f.tool && (
              <p className="text-small text-muted-foreground">In the call to {toolLabel(f.tool)}</p>
            )}
            {f.detail && (
              <p className="text-muted-foreground max-w-[68ch] break-words">{f.detail}</p>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
