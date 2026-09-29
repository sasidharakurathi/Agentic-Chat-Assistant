import type { Citation } from "@/lib/api";

/** Link targets that mean "citation marker n", not a real URL. */
export const CITE_PREFIX = "#cite-";

/** The answer's text as Markdown, with each citation marker turned into a
 *  `[n](#cite-n)` link that the renderer shows as a citation chip.
 *
 *  Positions come from the backend's `spans`, never from searching the text
 *  for `[n]`: the backend already knows which brackets are citations and
 *  which are code or indexing (`items[1]`), and the UI must not disagree.
 *  Replaced from the end backwards so earlier offsets stay valid; spans that
 *  overlap or fall outside the text are skipped. */
export function withCitationLinks(text: string, citations: Citation[]): string {
  const marks = citations
    .flatMap((c) => (c.spans ?? []).map(([start, end]) => ({ start, end, marker: c.marker })))
    .filter((m) => m.start >= 0 && m.end <= text.length && m.start < m.end)
    .sort((a, b) => a.start - b.start);

  const kept: typeof marks = [];
  let cursor = 0;
  for (const m of marks) {
    if (m.start < cursor) continue;
    kept.push(m);
    cursor = m.end;
  }

  let out = text;
  for (const m of kept.reverse()) {
    out = `${out.slice(0, m.start)}[${m.marker}](${CITE_PREFIX}${m.marker})${out.slice(m.end)}`;
  }
  return out;
}

/** The marker number of a citation link's href, or null for a real link. */
export function citeMarker(href: string | undefined): number | null {
  if (!href?.startsWith(CITE_PREFIX)) return null;
  const n = Number(href.slice(CITE_PREFIX.length));
  return Number.isInteger(n) && n > 0 ? n : null;
}
