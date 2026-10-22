"use client";

import { Activity } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { AssistantTraffic } from "@/lib/api";
import { formatCount } from "@/lib/format";

function plural(n: number, one: string, many: string): string {
  return `${formatCount(n)} ${n === 1 ? one : many}`;
}

/** The canvas's Traffic switch (Phase 10.2): off by default; on, the
 *  stations show the last day's live calls and the busy lines carry a
 *  travelling marker. */
export function TrafficToggle({ on, onChange }: { on: boolean; onChange: (on: boolean) => void }) {
  return (
    <Button
      type="button"
      size="sm"
      variant={on ? "default" : "outline"}
      aria-pressed={on}
      className="shadow-float pointer-events-auto shrink-0"
      title="Show the last 24 hours of live calls, errors and approvals on the canvas"
      onClick={() => onChange(!on)}
    >
      <Activity aria-hidden />
      Traffic
    </Button>
  );
}

/** What the drawing adds up to, while Traffic is on: a floating layer
 *  under the switch. */
export function TrafficSummary({
  traffic,
  error,
}: {
  traffic: AssistantTraffic | null;
  error: string | null;
}) {
  const t = traffic?.total;
  return (
    <div className="bg-card border-border shadow-float text-small pointer-events-auto flex w-full shrink-0 flex-col gap-1 rounded-lg border p-3">
      <p className="text-label font-medium">Traffic in the last 24 hours</p>
      {error && <p className="text-destructive">{error}</p>}
      {!t && !error && <p className="text-muted-foreground">Loading traffic…</p>}
      {t && (
        <>
          <p className="num">
            {plural(t.turns, "message", "messages")} answered, {plural(t.calls, "call", "calls")}
          </p>
          {(t.errors > 0 || t.turn_errors > 0) && (
            <p className="num text-destructive">
              {plural(t.errors + t.turn_errors, "error", "errors")}
            </p>
          )}
          {t.approvals > 0 && (
            <p className="num text-warning">
              {plural(t.approvals, "approval", "approvals")} asked for
            </p>
          )}
          {t.unplaced > 0 && (
            <p className="text-muted-foreground">
              {plural(t.unplaced, "call went", "calls went")} to things no longer on the canvas.
            </p>
          )}
        </>
      )}
      <p className="text-muted-foreground">
        Live chats only, never previews. Updates every 15 seconds.
      </p>
    </div>
  );
}
