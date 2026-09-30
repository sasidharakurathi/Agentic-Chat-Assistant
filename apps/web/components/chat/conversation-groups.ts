/** The conversation list's date groups (docs/DESIGN.md section 7, Chat):
 *  Today, Yesterday and Earlier, by the last message (or when the
 *  conversation started), in the viewer's own time zone. Groups keep the
 *  order the list arrived in and empty groups are left out. */

export type DatedConversation = { last_message_at?: string | null; created_at: string };

export type ConversationGroup<T> = { label: "Today" | "Yesterday" | "Earlier"; items: T[] };

function dayStart(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export function groupByDay<T extends DatedConversation>(
  rows: T[],
  now: Date = new Date(),
): ConversationGroup<T>[] {
  const today = dayStart(now);
  const yesterday = dayStart(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
  const groups: ConversationGroup<T>[] = [
    { label: "Today", items: [] },
    { label: "Yesterday", items: [] },
    { label: "Earlier", items: [] },
  ];
  for (const row of rows) {
    const when = new Date(row.last_message_at ?? row.created_at);
    const t = Number.isNaN(when.getTime()) ? 0 : when.getTime();
    // A timestamp from a clock slightly ahead still counts as today.
    if (t >= today) groups[0].items.push(row);
    else if (t >= yesterday) groups[1].items.push(row);
    else groups[2].items.push(row);
  }
  return groups.filter((g) => g.items.length > 0);
}
