"use client";

import Link from "next/link";
import { useState } from "react";

import { requestTone } from "@/components/approvals/RequestRow";
import { CodeBlock } from "@/components/chat/CodeBlock";
import { failureText } from "@/components/chat/error-text";
import { Button, buttonVariants } from "@/components/ui/button";
import { Lamp } from "@/components/ui/lamp";
import { approvals, type DecidableApproval } from "@/lib/api";
import { asText, closesIn, requestLine } from "@/lib/approvals";
import { formatDateTime } from "@/lib/format";

/** A request as it stands, read-only (Phase 9.2): what it is, who asked,
 *  who decided and what they said, what running it did. What it would do
 *  (the statement) shows only to whoever may see it; an employee who
 *  asked sees it as Chat shows it. The asker may withdraw one still
 *  waiting, and open the conversation it came from. */
export function RequestView({
  request,
  me,
  onChanged,
}: {
  request: DecidableApproval;
  me: string | undefined;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mine = Boolean(me) && request.requested_by === me;
  const asker = mine ? "You" : request.asker_name || request.asker_email || "Someone";
  const closes = request.status === "pending" ? closesIn(request.expires_at) : null;
  const values = Object.entries(request.input).filter(([, v]) => v !== null && v !== "");

  const withdraw = async () => {
    setBusy(true);
    setError(null);
    try {
      await approvals.withdraw(request.id);
      onChanged();
    } catch (err) {
      setError(failureText("Couldn't withdraw it.", err));
      setBusy(false);
    }
  };

  return (
    <article className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <h2 className="text-h2 min-w-0 flex-1 font-semibold break-words">
          {request.title ?? request.tool_name}
        </h2>
        <span role="status" className="inline-flex shrink-0 items-center gap-1.5">
          <Lamp tone={requestTone(request.status)} />
          {requestLine(request.status, request.tier, mine)}
        </span>
      </div>
      <p className="text-small text-muted-foreground">
        <span className="num">{request.reference}</span>
        {closes ? `, ${closes}` : ""}
      </p>

      <dl className="grid grid-cols-[minmax(0,auto)_1fr] gap-x-6 gap-y-2">
        <dt className="text-muted-foreground">Asked</dt>
        <dd>
          {asker}
          {request.assistant_name ? ` asked ${request.assistant_name}` : ""},{" "}
          {formatDateTime(request.created_at)}
          {request.preview ? " (in a preview)" : ""}
        </dd>
        {request.decided_at && (
          <>
            <dt className="text-muted-foreground">Decided</dt>
            <dd>
              {request.decided_by_name
                ? `${request.decided_by_name}, ${formatDateTime(request.decided_at)}`
                : formatDateTime(request.decided_at)}
              {request.note ? (
                <span className="text-muted-foreground block">“{request.note}”</span>
              ) : null}
            </dd>
          </>
        )}
        {request.executed_at && (
          <>
            <dt className="text-muted-foreground">Ran</dt>
            <dd>{formatDateTime(request.executed_at)}</dd>
          </>
        )}
        {values.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted-foreground">
              {request.fields?.find((f) => f.name === k)?.description || k.replace(/_/g, " ")}
            </dt>
            <dd className="break-words whitespace-pre-wrap">{asText(v)}</dd>
          </div>
        ))}
      </dl>

      {request.rationale && <CodeBlock code={request.rationale} label="What it runs" />}
      {request.result && (
        <div className="flex flex-col gap-1">
          <p className="text-muted-foreground">What happened</p>
          <p className="break-words whitespace-pre-wrap">{request.result}</p>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {request.can_withdraw && (
          <Button variant="outline" size="sm" disabled={busy} onClick={() => void withdraw()}>
            {busy ? "Withdrawing…" : "Withdraw"}
          </Button>
        )}
        {mine && request.conversation_id && (
          <Link
            href={`/chat/c/${request.conversation_id}`}
            className={buttonVariants({ size: "sm", variant: "outline" })}
          >
            Open the conversation
          </Link>
        )}
      </div>
      {error && (
        <p role="alert" className="text-small text-destructive">
          {error}
        </p>
      )}
    </article>
  );
}
