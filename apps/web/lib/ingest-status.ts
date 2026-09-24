import type { DataSource } from "@/lib/api";
import { formatUsd } from "@/lib/format";

const STAGE_LABEL: Record<string, string> = {
  fetching: "Fetching",
  chunking: "Splitting into chunks",
  contextualizing: "Writing context",
  embedding: "Embedding",
  writing: "Saving",
};

/** "Embedding 3/12" for a source that is being indexed, else null. */
export function progressLabel(row: Pick<DataSource, "status" | "progress">): string | null {
  const p = row.progress;
  if (row.status !== "processing" || !p) return null;
  const stage = STAGE_LABEL[p.stage] ?? p.stage;
  return p.total ? `${stage} ${p.done ?? 0}/${p.total}` : `${stage}…`;
}

/** One line on what the last indexing did, e.g.
 *  "12 embedded, 40 reused · context for 30 · $0.0012". */
export function reportLabel(row: Pick<DataSource, "status" | "ingest_report">): string | null {
  const r = row.ingest_report;
  if (row.status !== "ready" || !r) return null;
  const parts = [
    r.reused_embeddings > 0
      ? `${r.embedded} embedded, ${r.reused_embeddings} reused`
      : `${r.embedded} embedded`,
  ];
  const context = r.contextualized + r.reused_context;
  if (context > 0) parts.push(`context for ${context}`);
  if (r.context_failed > 0) parts.push(`${r.context_failed} without context`);
  parts.push(r.cost_usd > 0 ? formatUsd(r.cost_usd) : "no cost");
  return parts.join(" · ");
}
