"use client";

import { useState } from "react";

import { ApiError, conversations, type Run } from "@/lib/api";

/** What one answer cost and how it ran — read from its `runs` row, which
 *  until now was written on every turn and read by nothing. Fetched only
 *  when opened: most answers are never inspected. */
export function RunDetails({
  conversationId,
  messageId,
}: {
  conversationId: string;
  messageId: string;
}) {
  const [open, setOpen] = useState(false);
  const [run, setRun] = useState<Run | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || run !== undefined) return;
    try {
      const page = await conversations.runs(conversationId, messageId);
      setRun(page.items[0] ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load run details");
    }
  }

  return (
    <div className="text-xs">
      <button
        className="text-muted-foreground hover:text-foreground"
        aria-expanded={open}
        onClick={() => void toggle()}
      >
        {open ? "Hide run details" : "Run details"}
      </button>
      {open && (
        <div className="border-border mt-1 rounded border p-2">
          {error && <p className="text-destructive">{error}</p>}
          {!error && run === undefined && <p className="text-muted-foreground">Loading…</p>}
          {!error && run === null && (
            <p className="text-muted-foreground">No run was recorded for this message.</p>
          )}
          {run && (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
              <dt className="text-muted-foreground">Status</dt>
              <dd>
                {run.status}
                {run.error ? ` — ${run.error}` : ""}
              </dd>
              <dt className="text-muted-foreground">Version</dt>
              <dd>{run.version_number != null ? `v${run.version_number}` : "draft"}</dd>
              <dt className="text-muted-foreground">Model</dt>
              <dd>
                {run.model ?? "—"}
                {run.effort ? ` · ${run.effort}` : ""}
                {run.driver ? ` · ${run.driver} driver` : ""}
              </dd>
              <dt className="text-muted-foreground">Tokens</dt>
              <dd>
                {run.tokens_in.toLocaleString()} in · {run.tokens_out.toLocaleString()} out ·{" "}
                {run.num_turns} model {run.num_turns === 1 ? "turn" : "turns"}
              </dd>
              <dt className="text-muted-foreground">Cost</dt>
              <dd>${Number(run.cost_usd).toFixed(4)}</dd>
              <dt className="text-muted-foreground">Duration</dt>
              <dd>{run.duration_ms != null ? `${(run.duration_ms / 1000).toFixed(2)}s` : "—"}</dd>
              <dt className="text-muted-foreground">Trace</dt>
              <dd className="font-mono break-all select-all">{run.trace_id ?? "—"}</dd>
            </dl>
          )}
        </div>
      )}
    </div>
  );
}
