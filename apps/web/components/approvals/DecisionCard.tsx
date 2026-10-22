"use client";

import { ChevronRight } from "lucide-react";
import { useId, useState } from "react";

import { CodeBlock } from "@/components/chat/CodeBlock";
import { failureText } from "@/components/chat/error-text";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { LineBullet } from "@/components/ui/line-bullet";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, approvals, type DecidableApproval } from "@/lib/api";
import { approvalQuestion, asText, closesIn, inputAddsDetail, riskLabel } from "@/lib/approvals";
import { useAuth } from "@/lib/auth";
import { formatRelative } from "@/lib/format";
import { toolNodeType } from "@/lib/tool-label";
import { cn } from "@/lib/utils";

/** One request in the approver's queue (Phase 9.1): who asked, in which
 *  assistant, exactly what would run, and the decision. Declining needs a
 *  note: the person who asked is told it. The card shows the request
 *  itself, never the conversation, and nothing the model wrote to argue
 *  for it. */
export function DecisionCard({
  request,
  onDecided,
}: {
  request: DecidableApproval;
  /** Decided here or elsewhere: the list looks again. */
  onDecided: (message: string) => void;
}) {
  const headingId = useId();
  const noteId = useId();
  const inputId = useId();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approved" | "denied" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showInput, setShowInput] = useState(false);
  const high = request.risk === "high";
  const { user } = useAuth();
  // Listed only when nobody else can decide it (7b.3's switch): say why.
  const own = Boolean(user?.id) && request.requested_by === user?.id;
  const asker = own ? "You" : request.asker_name || request.asker_email || "Someone";
  const closes = closesIn(request.expires_at);

  const decide = async (decision: "approved" | "denied") => {
    if ((decision === "denied" || own) && !note.trim()) {
      setError(
        own
          ? "Say why you're deciding your own request: it's recorded."
          : "Say why you're declining: the person who asked is told.",
      );
      return;
    }
    setBusy(decision);
    setError(null);
    try {
      await approvals.resolve(request.id, {
        decision,
        input_sha256: request.input_sha256,
        note: note.trim() || undefined,
      });
      onDecided(
        own
          ? `${request.reference} ${decision === "approved" ? "approved. It runs now." : "declined."}`
          : decision === "approved"
            ? `${request.reference} approved. It runs now, and ${asker} is told how it went.`
            : `${request.reference} declined. ${asker} is told why.`,
      );
    } catch (err) {
      const closed = ["approval_not_pending", "approval_expired", "input_changed"];
      if (err instanceof ApiError && closed.includes(err.code)) {
        onDecided(err.message);
        return;
      }
      setError(failureText("Couldn't record your decision.", err));
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
  const decline = (
    <Button
      size="sm"
      variant={high ? "default" : "outline"}
      disabled={busy !== null}
      onClick={() => void decide("denied")}
    >
      {busy === "denied" ? "Declining…" : "Decline"}
    </Button>
  );

  return (
    <section
      aria-labelledby={headingId}
      className={cn(
        "bg-card border-border flex flex-col gap-3 rounded-lg border border-l-4 p-4",
        high ? "border-l-destructive" : "border-l-warning",
      )}
    >
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <LineBullet type={toolNodeType(request.tool_name)} size={20} className="mt-px" />
        <div className="min-w-0 flex-1">
          <h3 id={headingId} className="text-h3 font-semibold break-words">
            {request.title || approvalQuestion(request.tool_name)}
          </h3>
          <p className="text-muted-foreground">
            {asker} asked {request.assistant_name ?? "an assistant"}
            {request.preview ? " (in a preview)" : ""}, {formatRelative(request.created_at)}
          </p>
        </div>
        <Badge variant={high ? "destructive" : "warning"}>{riskLabel(request.risk)}</Badge>
      </div>
      <p className="text-small text-muted-foreground">
        <span className="num">{request.reference}</span>
        {closes ? `, ${closes}` : ""}
      </p>

      {request.action_name && <ActionValues input={request.input} fields={request.fields} />}
      {request.rationale && <CodeBlock code={request.rationale} label="What would run" />}
      {!request.action_name && inputAddsDetail(request.rationale ?? "", request.input) && (
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
                code={JSON.stringify(request.input, null, 2)}
                label="Everything it would send"
              />
            </div>
          )}
        </div>
      )}

      <div className="flex flex-col gap-1">
        <Label htmlFor={noteId}>
          {own ? (
            <>
              Why you&apos;re deciding your own{" "}
              <span className="text-muted-foreground">(nobody else can; it&apos;s recorded)</span>
            </>
          ) : (
            <>
              Note for {asker} <span className="text-muted-foreground">(needed to decline)</span>
            </>
          )}
        </Label>
        <Textarea
          id={noteId}
          value={note}
          maxLength={1000}
          onChange={(e) => setNote(e.target.value)}
          placeholder={own ? "Why it can't wait for someone else" : "What they should know"}
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {/* At high risk the safe answer comes first. */}
        {high ? (
          <>
            {decline}
            {approve}
          </>
        ) : (
          <>
            {approve}
            {decline}
          </>
        )}
      </div>
      <p className="text-small text-muted-foreground">
        Approving runs it once, as {own ? "you" : asker}, after checking it is still allowed.
      </p>
      {error && (
        <p role="alert" className="text-small text-destructive">
          {error}
        </p>
      )}
    </section>
  );
}

function ActionValues({
  input,
  fields = [],
}: {
  input: Record<string, unknown>;
  fields?: { name: string; description?: string }[];
}) {
  const entries = Object.entries(input).filter(([, v]) => v !== null && v !== "");
  if (entries.length === 0) return null;
  return (
    <dl className="grid grid-cols-[minmax(0,auto)_1fr] gap-x-4 gap-y-1">
      {entries.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">
            {fields.find((f) => f.name === k)?.description || k.replace(/_/g, " ")}
          </dt>
          <dd className="break-words whitespace-pre-wrap">{asText(v)}</dd>
        </div>
      ))}
    </dl>
  );
}
