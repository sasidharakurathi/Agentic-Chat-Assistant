import type { DataSource } from "@/lib/api";
import { formatUsd } from "@/lib/format";

const STAGE_LABEL: Record<string, string> = {
  fetching: "Fetching",
  chunking: "Splitting into passages",
  contextualizing: "Adding context",
  embedding: "Embedding passages",
  writing: "Saving",
};

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** "Embedding passages: 3 of 12" for a source that is being indexed, else
 *  null. An unknown stage is shown as the server names it. */
export function progressLabel(row: Pick<DataSource, "status" | "progress">): string | null {
  const p = row.progress;
  if (row.status !== "processing" || !p) return null;
  const stage = STAGE_LABEL[p.stage] ?? p.stage;
  return p.total ? `${stage}: ${p.done ?? 0} of ${p.total}` : `${stage}…`;
}

/** What the last indexing did, in a sentence or two, e.g.
 *  "12 passages embedded, 40 reused. Cost $0.0012." Internal counts the
 *  person cannot act on (context written, embedding model) are left out;
 *  passages that got no context are kept, because they search worse. */
export function reportLabel(row: Pick<DataSource, "status" | "ingest_report">): string | null {
  const r = row.ingest_report;
  if (row.status !== "ready" || !r) return null;
  const embedded = plural(r.embedded, "passage", "passages");
  const sentences = [
    r.reused_embeddings > 0
      ? `${embedded} embedded, ${r.reused_embeddings} reused.`
      : `${embedded} embedded.`,
  ];
  if (r.context_failed > 0) {
    sentences.push(`${plural(r.context_failed, "passage", "passages")} got no context.`);
  }
  sentences.push(r.cost_usd > 0 ? `Cost ${formatUsd(r.cost_usd)}.` : "No cost.");
  return sentences.join(" ");
}
