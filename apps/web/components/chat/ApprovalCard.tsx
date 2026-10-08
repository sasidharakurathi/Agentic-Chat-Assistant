"use client";

import { ChevronRight } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { CodeBlock } from "@/components/chat/CodeBlock";
import { failureText } from "@/components/chat/error-text";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { LineBullet } from "@/components/ui/line-bullet";
import {
  approvalQuestion,
  formatCountdown,
  inputAddsDetail,
  riskLabel,
  URGENT_S,
} from "@/lib/approvals";
import { toolNodeType } from "@/lib/tool-label";
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

/** A tool call waiting on a person (docs/DESIGN.md section 7): a plate with
 *  an ochre left bar (red at high risk), the question in plain words, the
 *  exact statement that would run, and how long is left. At high risk the
 *  safe answer is the prominent one: Deny is the default button and
 *  approving reads "Approve and run". */
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
  const [showInput, setShowInput] = useState(false);
  const headingId = useId();
  const inputId = useId();
  const secondsLeft = useCountdown(approval.expires_at);
  const expired = secondsLeft !== null && secondsLeft <= 0;
  const high = approval.risk === "high";

  const decide = async (decision: "approved" | "denied") => {
    setBusy(decision);
    setError(null);
    try {
      await approvals.resolve(approval.approval_id, decision);
      onDecided(decision);
    } catch (err) {
      // Already decided, or past its expiry (Phase 7a.7): either way it is
      // closed, and the card goes.
      const closed = ["approval_not_pending", "approval_expired"];
      if (err instanceof ApiError && closed.includes(err.code) && onGone) {
        onGone(err.message);
        return;
      }
      // Anything else: say what happened, rather than "something went wrong".
      setError(failureText("Couldn't record your answer.", err));
      setBusy(null);
    }
  };

  const approve = (
    <Button
      size="sm"
      variant={high ? "destructive" : "default"}
      disabled={busy !== null}
      onClick={() => void decide("approved")}
    >
      {busy === "approved" ? "Approving…" : high ? "Approve and run" : "Approve"}
    </Button>
  );
  const deny = (
    <Button
      size="sm"
      variant={high ? "default" : "outline"}
      disabled={busy !== null}
      onClick={() => void decide("denied")}
    >
      {busy === "denied" ? "Denying…" : "Deny"}
    </Button>
  );

  return (
    <section
      aria-labelledby={headingId}
      className={cn(
        "bg-card border-border flex max-w-[72ch] flex-col gap-3 rounded-lg border border-l-4 py-4 pr-4 pl-4",
        expired ? "border-l-border" : high ? "border-l-destructive" : "border-l-warning",
      )}
    >
      {/* Announced once, when the card appears; the countdown below is a
          timer, which screen readers don't read out every second. */}
      <div role="alert" className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <LineBullet type={toolNodeType(approval.tool)} size={20} className="mt-px" />
        <h3 id={headingId} className="text-h3 min-w-0 flex-1 font-semibold break-words">
          {approvalQuestion(approval.tool)}
        </h3>
        <Badge variant={RISK_VARIANT[approval.risk]}>{riskLabel(approval.risk)}</Badge>
      </div>

      {/* The exact statement, never a summary of it. "Approve a database
          write?" is unanswerable; this is answerable. */}
      {approval.rationale && <CodeBlock code={approval.rationale} label="What would run" />}

      {/* The full input, when it says more than the statement above: an
          MCP tool's arguments, an HTTP request's body. */}
      {inputAddsDetail(approval.rationale ?? "", approval.input) && (
        <div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="group -ml-2"
            aria-expanded={showInput}
            aria-controls={inputId}
            onClick={() => setShowInput((v) => !v)}
          >
            <ChevronRight
              aria-hidden
              className="transition-transform duration-120 ease-out group-aria-expanded:rotate-90 motion-reduce:transition-none"
            />
            Everything it would send
          </Button>
          {showInput && (
            <div id={inputId} className="mt-1">
              <CodeBlock
                code={JSON.stringify(approval.input, null, 2)}
                label="Everything it would send"
              />
            </div>
          )}
        </div>
      )}

      {expired ? (
        <p className="text-muted-foreground">
          This request timed out and was declined automatically.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <div className="flex items-center gap-2">
              {high ? (
                <>
                  {deny}
                  {approve}
                </>
              ) : (
                <>
                  {approve}
                  {deny}
                </>
              )}
            </div>
            {secondsLeft !== null && (
              <span
                role="timer"
                className={cn(
                  "num text-small",
                  secondsLeft <= URGENT_S
                    ? "text-destructive font-semibold"
                    : "text-muted-foreground",
                )}
                aria-label={`${formatCountdown(secondsLeft)} left to answer`}
              >
                {formatCountdown(secondsLeft)} left
              </span>
            )}
          </div>
          <p className="text-small text-muted-foreground">
            Nothing runs until you choose. If nobody answers in time, it is denied.
          </p>
        </div>
      )}

      {error && (
        <p role="alert" className="text-small text-destructive">
          {error}
        </p>
      )}
    </section>
  );
}
