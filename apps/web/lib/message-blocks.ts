import type { Citation, GuardrailFinding, MessageBlock } from "@/lib/api";
import type { ToolCallView } from "@/lib/tool-tree";

/** A saved message's blocks, by kind.
 *
 *  Tool calls are "anything that is not a known other kind", on purpose:
 *  messages persisted before task 2.9 have blocks with no `type` key at all,
 *  so filtering for `type === "tool_call"` would silently stop rendering
 *  tool cards across every existing conversation. Every new kind of block
 *  (citations in 2.9, guardrail findings in 5.3, screenshots in 9.6) must
 *  be listed here, or it shows up as an empty tool card. */
const NOT_TOOLS = new Set(["citation", "guardrail", "image"]);

export function splitBlocks(blocks?: MessageBlock[]): {
  tools: ToolCallView[];
  cites: Citation[];
  guards: GuardrailFinding[];
} {
  const all = (blocks ?? []) as Array<Record<string, unknown>>;
  return {
    tools: all.filter((b) => !NOT_TOOLS.has(String(b?.type ?? ""))) as unknown as ToolCallView[],
    cites: all.filter((b) => b?.type === "citation") as unknown as Citation[],
    guards: all.filter((b) => b?.type === "guardrail") as unknown as GuardrailFinding[],
  };
}

/** The screenshots sent with a message (Phase 9.6), by attachment id. */
export function imageIds(blocks?: MessageBlock[]): string[] {
  const all = (blocks ?? []) as Array<Record<string, unknown>>;
  return all
    .filter((b) => b?.type === "image" && typeof b.attachment_id === "string")
    .map((b) => String(b.attachment_id));
}

/** How a guardrail finding is headed in the chat. */
export function guardrailTitle(f: Pick<GuardrailFinding, "check" | "where">): string {
  switch (f.check) {
    case "injection":
      return f.where === "user_message"
        ? "Instruction override attempt"
        : "Instructions found in a tool result";
    case "exfiltration":
      return "Credential kept from leaving";
    case "pii":
      return "Personal data removed";
    case "schema":
      return "Invalid tool input refused";
    case "budget":
      return "Budget reached";
    case "secret":
      return "Password or key hidden";
    default:
      return "Guardrail";
  }
}
