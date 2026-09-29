/** A tool call as the chat shows it. `parent_id` is set on calls a subagent
 *  made, naming the delegation call it ran under; `subagent_text` is that
 *  subagent's own notes, kept on the delegation call (task 2.10). */
export type ToolCallView = {
  id: string;
  name: string;
  input: Record<string, unknown>;
  status?: string;
  output?: string;
  parent_id?: string | null;
  subagent_text?: string;
  /** How it was permitted (task 4.7): auto, approved, declined, expired,
   *  interrupted or refused. Absent for calls that never ask. */
  permission?: string | null;
};

const PERMISSION_TEXT: Record<string, string> = {
  auto: "Ran without asking",
  approved: "Approved by a person",
  declined: "Declined by a person",
  expired: "Declined: nobody answered in time",
  interrupted: "Stopped before anyone answered",
  refused: "Not allowed by this assistant's rules",
};

/** The audit line a tool card shows, or null. */
export function permissionText(permission?: string | null): string | null {
  return permission ? (PERMISSION_TEXT[permission] ?? permission) : null;
}

/** Top-level calls in order, and each delegation call's nested calls. A call
 *  whose parent is not in the list (a partial stream) stays top-level rather
 *  than disappearing. */
export function nestCalls(calls: ToolCallView[]): {
  top: ToolCallView[];
  children: Map<string, ToolCallView[]>;
} {
  const ids = new Set(calls.map((c) => c.id));
  const top: ToolCallView[] = [];
  const children = new Map<string, ToolCallView[]>();
  for (const call of calls) {
    const parent = call.parent_id;
    if (parent && ids.has(parent)) {
      children.set(parent, [...(children.get(parent) ?? []), call]);
    } else {
      top.push(call);
    }
  }
  return { top, children };
}

/** A subagent's streamed text, appended to its delegation call. */
export function appendSubagentText(
  calls: ToolCallView[],
  parentId: string,
  text: string,
): ToolCallView[] {
  return calls.map((c) =>
    c.id === parentId ? { ...c, subagent_text: (c.subagent_text ?? "") + text } : c,
  );
}
