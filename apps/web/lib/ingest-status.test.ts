import { describe, expect, it } from "vitest";

import { progressLabel, reportLabel } from "./ingest-status";

const report = {
  chunks: 52,
  embedding_model: "voyage-3-large",
  embedded: 12,
  reused_embeddings: 40,
  contextualized: 12,
  reused_context: 40,
  context_failed: 0,
  context_skipped: null,
  cost_usd: 0.0012,
};

describe("progressLabel", () => {
  it("shows the stage and count while processing", () => {
    expect(
      progressLabel({ status: "processing", progress: { stage: "embedding", done: 3, total: 12 } }),
    ).toBe("Embedding 3/12");
    expect(
      progressLabel({
        status: "processing",
        progress: { stage: "fetching", done: null, total: null },
      }),
    ).toBe("Fetching…");
  });

  it("is null once the source is no longer processing", () => {
    expect(
      progressLabel({ status: "ready", progress: { stage: "embedding", done: 3, total: 12 } }),
    ).toBeNull();
    expect(progressLabel({ status: "processing", progress: null })).toBeNull();
  });
});

describe("reportLabel", () => {
  it("summarises reuse, context and cost", () => {
    expect(reportLabel({ status: "ready", ingest_report: report })).toBe(
      "12 embedded, 40 reused · context for 52 · $0.0012",
    );
  });

  it("says when a reindex cost nothing", () => {
    expect(
      reportLabel({
        status: "ready",
        ingest_report: { ...report, embedded: 0, reused_embeddings: 52, cost_usd: 0 },
      }),
    ).toBe("0 embedded, 52 reused · context for 52 · no cost");
  });

  it("is null without a report or before the source is ready", () => {
    expect(reportLabel({ status: "ready", ingest_report: null })).toBeNull();
    expect(reportLabel({ status: "error", ingest_report: report })).toBeNull();
  });
});
