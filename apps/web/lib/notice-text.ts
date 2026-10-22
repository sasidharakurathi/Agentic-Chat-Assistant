/** The privacy notice's text as blocks to show (Phase 8.7). The owner
 *  writes plain text: paragraphs apart by a blank line, list items starting
 *  "- ", and a short line just above a list is that list's heading. It is
 *  shown as text, never as markup. */

export type NoticeBlock =
  | { kind: "heading"; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; items: string[] };

export function noticeBlocks(body: string): NoticeBlock[] {
  const blocks: NoticeBlock[] = [];
  for (const chunk of body.replace(/\r\n/g, "\n").split(/\n\s*\n/)) {
    const lines = chunk
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
    let prose: string[] = [];
    let items: string[] = [];
    const flushProse = () => {
      if (prose.length) blocks.push({ kind: "paragraph", text: prose.join(" ") });
      prose = [];
    };
    const flushItems = () => {
      if (items.length) blocks.push({ kind: "list", items });
      items = [];
    };
    lines.forEach((line, i) => {
      if (line.startsWith("- ")) {
        flushProse();
        items.push(line.slice(2).trim());
        return;
      }
      flushItems();
      const nextIsItem = lines[i + 1]?.startsWith("- ") ?? false;
      if (nextIsItem && prose.length === 0 && line.length <= 60 && !/[.:!?]$/.test(line)) {
        blocks.push({ kind: "heading", text: line });
        return;
      }
      prose.push(line);
    });
    flushProse();
    flushItems();
  }
  return blocks;
}

/** A "mailto:" link to the privacy contact, with a subject; null when
 *  there is no address to write to. */
export function contactLink(email: string, subject: string): string | null {
  if (!/^[^\s@]+@[^\s@]+$/.test(email)) return null;
  return `mailto:${email}?subject=${encodeURIComponent(subject)}`;
}
