"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatCountdown, inputAddsDetail, URGENT_S } from "@/lib/approvals";
import { toolLabel } from "@/lib/tool-label";
import { ApiError, approvals, type ApprovalRisk } from "@/lib/api";
import { cn } from "@/lib/utils";

export type PendingApproval = {
  approval_id: string;
  tool: string;
  input: Record<string, unknown>;
  risk: ApprovalRisk;
  /** The exact thing that will run — for SQL, the statement itself. */
  rationale: string;
  expires_at: string | null;
};

const RISK_VARIANT: Record<ApprovalRisk, "muted" | "warning" | "destructive"> = {
  low: "muted",
  medium: "warning",
  high: "destructive",
};

function useCountdown(expiresAt: string | null): number | null {
  const [left, setLeft] = useState<number | null>(null);
  useEffect(() => {
    if (!expiresAt) return;
    const end = new Date(expiresAt).getTime();
    const tick = () => setLeft(Math.max(0, Math.round((end - Date.now()) / 1000)));
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [expiresAt]);
  return left;
}

export function ApprovalCard({
  approval,
  onDecided,
  onGone,
}: {
  approval: PendingApproval;
  onDecided: (decision: "approved" | "denied") => void;
  /** The server no longer has this approval pending (it expired, the turn
   *  was stopped, or someone else answered): the card should go. */
  onGone?: (reason: string) => void;
}) {
  const [busy, setBusy] = useState<"approved" | "denied" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const secondsLeft = useCountdown(approval.expires_at);
  const expired = secondsLeft !== null && secondsLeft <= 0;

  const decide = async (decision: "approved" | "denied") => {
    setBusy(decision);
    setError(null);
    try {
      await approvals.resolve(approval.approval_id, decision);
      onDecided(decision);
    } catch (err) {
      if (err instanceof ApiError && err.code === "approval_not_pending" && onGone) {
        onGone(err.message);
        return;
      }
      // Anything else: say what happened, rather than "something went wrong".
      setError(err instanceof ApiError ? err.message : "Could not record that decision.");
      setBusy(null);
    }
  };

  const tool = toolLabel(approval.tool);

  return (
    <div
      className={cn(
        "rounded-md border-2 px-3 py-2.5 text-xs",
        expired ? "border-border bg-muted/40" : "border-warning bg-warning/5",
      )}
      role="alert"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">Approval needed — {tool}</span>
        <div className="flex items-center gap-2">
          <Badge variant={RISK_VARIANT[approval.risk]}>{approval.risk} risk</Badge>
          {secondsLeft !== null && !expired && (
            <span
              className={cn(
                "tabular-nums",
                secondsLeft <= URGENT_S
                  ? "text-destructive font-semibold"
                  : "text-muted-foreground",
              )}
              aria-label={`${formatCountdown(secondsLeft)} left to answer`}
            >
              {formatCountdown(secondsLeft)}
            </span>
          )}
        </div>
      </div>

      {/* The exact statement, never a summary of it. "Approve a database
          write?" is unanswerable; this is answerable. */}
      {approval.rationale && (
        <pre className="border-border bg-background mt-2 overflow-auto rounded border px-2 py-1.5 font-mono text-[11px] whitespace-pre-wrap">
          {approval.rationale}
        </pre>
      )}

      {/* The full input, when it says more than the statement above: an
          MCP tool's arguments, an HTTP request's body (task 5.9). */}
      {inputAddsDetail(approval.rationale ?? "", approval.input) && (
        <details className="mt-2">
          <summary className="text-muted-foreground cursor-pointer">
            Everything it would send
          </summary>
          <pre className="border-border bg-background mt-1 max-h-48 overflow-auto rounded border px-2 py-1.5 font-mono text-[11px] whitespace-pre-wrap">
            {JSON.stringify(approval.input, null, 2)}
          </pre>
        </details>
      )}

      {expired ? (
        <p className="text-muted-foreground mt-2">
          This request timed out and was declined automatically.
        </p>
      ) : (
        <div className="mt-2 flex items-center gap-2">
          <Button size="sm" disabled={busy !== null} onClick={() => void decide("approved")}>
            {busy === "approved" ? "Approving…" : "Approve"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy !== null}
            onClick={() => void decide("denied")}
          >
            {busy === "denied" ? "Denying…" : "Deny"}
          </Button>
          <span className="text-muted-foreground">
            Nothing runs until you choose; no answer means denied.
          </span>
        </div>
      )}

      {error && <p className="text-destructive mt-2">{error}</p>}
    </div>
  );
}
