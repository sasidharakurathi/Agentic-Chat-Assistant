"use client";

import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { useId, useState, type FormEvent } from "react";

import { CodeBlock } from "@/components/chat/CodeBlock";
import { failureText } from "@/components/chat/error-text";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LineBullet } from "@/components/ui/line-bullet";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, approvals, type ActionField, type Approval } from "@/lib/api";
import {
  approvalQuestion,
  asText,
  closesIn,
  editedParams,
  inputAddsDetail,
  requestLine,
} from "@/lib/approvals";
import { toolNodeType } from "@/lib/tool-label";
import { cn } from "@/lib/utils";

/** A request in the thread (docs/DESIGN.md section 7; Phase 9.1): a plate
 *  with an ochre left bar (red at high risk), what it is in plain words,
 *  where it stands, and what the reader can do about it.
 *
 *  Nothing waits inside the answer any more: the request is stored, and the
 *  card is how the person asking confirms their own, or sees that it waits
 *  for an approver, or that it is running. How it ended is posted into the
 *  conversation, and the card goes. */
export function ApprovalCard({
  request,
  mine,
  employee = false,
  onChanged,
  onGone,
}: {
  request: Approval;
  /** The person reading asked for it. */
  mine: boolean;
  /** The reader is an employee: what would run is never sent to them. */
  employee?: boolean;
  /** Decided or withdrawn: look again. */
  onChanged: () => void;
  /** No longer open on the server (decided elsewhere, expired, changed). */
  onGone?: (reason: string) => void;
}) {
  const headingId = useId();
  const inputId = useId();
  const [busy, setBusy] = useState<"approved" | "denied" | "withdraw" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showInput, setShowInput] = useState(false);
  const fields = request.fields ?? [];
  const [values, setValues] = useState<Record<string, string | boolean>>(() =>
    Object.fromEntries(
      fields.map((f) => [
        f.name,
        f.type === "boolean" ? request.input[f.name] === true : asText(request.input[f.name]),
      ]),
    ),
  );
  const high = request.risk === "high";
  const pending = request.status === "pending";
  const confirmHere = pending && mine && request.tier === "asker";
  const editable = confirmHere && fields.length > 0;
  const heading = request.title || approvalQuestion(request.tool_name);
  const closes = pending ? closesIn(request.expires_at) : null;

  const fail = (err: unknown, what: string) => {
    const closed = ["approval_not_pending", "approval_expired", "input_changed"];
    if (err instanceof ApiError && closed.includes(err.code) && onGone) {
      onGone(err.message);
      return;
    }
    setError(failureText(what, err));
    setBusy(null);
  };

  const decide = async (decision: "approved" | "denied") => {
    setBusy(decision);
    setError(null);
    try {
      await approvals.resolve(request.id, {
        decision,
        input_sha256: request.input_sha256,
        params: decision === "approved" && editable ? editedParams(fields, values) : undefined,
      });
      onChanged();
    } catch (err) {
      fail(err, "Couldn't record your answer.");
    }
  };

  const withdraw = async () => {
    setBusy("withdraw");
    setError(null);
    try {
      await approvals.withdraw(request.id);
      onChanged();
    } catch (err) {
      fail(err, "Couldn't withdraw it.");
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void decide("approved");
  };

  return (
    <section
      aria-labelledby={headingId}
      className={cn(
        "bg-card border-border flex max-w-[72ch] flex-col gap-3 rounded-lg border border-l-4 p-4",
        !pending ? "border-l-border" : high ? "border-l-destructive" : "border-l-warning",
      )}
    >
      {/* Announced once, when the card appears. */}
      <div
        role={confirmHere ? "alert" : "status"}
        className="flex flex-wrap items-start gap-x-3 gap-y-2"
      >
        <LineBullet type={toolNodeType(request.tool_name)} size={20} className="mt-px" />
        <h3 id={headingId} className="text-h3 min-w-0 flex-1 font-semibold break-words">
          {heading}
        </h3>
        <Badge variant={!pending ? "muted" : high ? "destructive" : "warning"}>
          {requestLine(request.status, request.tier, mine)}
        </Badge>
      </div>
      <p className="text-small text-muted-foreground">
        <span className="num">{request.reference}</span>
        {closes ? `, ${closes}` : ""}
      </p>

      {editable ? (
        <form id={`${headingId}-form`} onSubmit={submit} className="flex flex-col gap-3">
          {fields.map((f) => (
            <FieldInput
              key={f.name}
              field={f}
              value={values[f.name]}
              onChange={(v) => setValues((old) => ({ ...old, [f.name]: v }))}
            />
          ))}
        </form>
      ) : request.action_name ? (
        <Values input={request.input} fields={fields} />
      ) : (
        !employee &&
        request.rationale && (
          // The exact statement, never a summary of it.
          <CodeBlock code={request.rationale} label="What would run" />
        )
      )}

      {!employee &&
        !request.action_name &&
        inputAddsDetail(request.rationale ?? "", request.input) && (
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

      {confirmHere ? (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              type={editable ? "submit" : "button"}
              form={editable ? `${headingId}-form` : undefined}
              variant={high ? "destructive" : "default"}
              disabled={busy !== null}
              onClick={editable ? undefined : () => void decide("approved")}
            >
              {busy === "approved" ? "Confirming…" : "Confirm"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy !== null}
              onClick={() => void decide("denied")}
            >
              {busy === "denied" ? "Cancelling…" : "Don't do it"}
            </Button>
          </div>
          <p className="text-small text-muted-foreground">
            Nothing happens until you confirm. Check the details first
            {editable ? ", and change them if they're wrong." : "."}
          </p>
        </div>
      ) : pending && request.tier === "approver" ? (
        <div className="flex flex-col gap-2">
          <p className="text-muted-foreground">
            {mine
              ? "An approver decides this. You'll get an email when they have, and the outcome shows here."
              : "It waits for one of this assistant's approvers."}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {mine && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy !== null}
                onClick={() => void withdraw()}
              >
                {busy === "withdraw" ? "Withdrawing…" : "Withdraw"}
              </Button>
            )}
            {!employee && !mine && (
              <Link
                href="/approvals"
                className={buttonVariants({ size: "sm", variant: "outline" })}
              >
                Decide it in Approvals
              </Link>
            )}
          </div>
        </div>
      ) : (
        !pending && (
          <p role="status" className="text-muted-foreground">
            {request.status === "executing" ? "Running it now." : "Approved. It runs in a moment."}
          </p>
        )
      )}

      {error && (
        <p role="alert" className="text-small text-destructive">
          {error}
        </p>
      )}
    </section>
  );
}

/** A declared action's values, as given. */
function Values({ input, fields }: { input: Record<string, unknown>; fields: ActionField[] }) {
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

/** One value the asker may correct before confirming. */
function FieldInput({
  field,
  value,
  onChange,
}: {
  field: ActionField;
  value: string | boolean | undefined;
  onChange: (v: string | boolean) => void;
}) {
  const id = useId();
  const label = field.description || field.name.replace(/_/g, " ");
  if (field.type === "boolean") {
    return (
      <label htmlFor={id} className="flex items-center gap-2">
        <input
          id={id}
          type="checkbox"
          className="size-4"
          checked={value === true}
          onChange={(e) => onChange(e.target.checked)}
        />
        {label}
      </label>
    );
  }
  const text = typeof value === "string" ? value : "";
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id}>
        {label}
        {!field.required && <span className="text-muted-foreground"> (optional)</span>}
      </Label>
      {field.choices && field.choices.length > 0 ? (
        <Select id={id} value={text} onChange={(e) => onChange(e.target.value)}>
          {!field.required && <option value="">None</option>}
          {field.choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
      ) : field.type === "string" && (field.max_length ?? 500) > 200 ? (
        <Textarea
          id={id}
          value={text}
          maxLength={field.max_length ?? 500}
          required={field.required}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <Input
          id={id}
          value={text}
          required={field.required}
          type={field.type === "date" ? "date" : field.type === "string" ? "text" : "number"}
          step={field.type === "number" ? "any" : undefined}
          maxLength={field.type === "string" ? (field.max_length ?? 500) : undefined}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </div>
  );
}
