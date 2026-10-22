"use client";

import { useCallback, useEffect, useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SectionHeading } from "@/components/ui/section-heading";
import { ApiError, approvals, type Delegation } from "@/lib/api";
import { formatDate } from "@/lib/format";

/** Handing your requests to someone while you're away (Phase 9.1). They
 *  decide what you could as an assistant's named approver, until the date;
 *  an admin's own powers don't pass on. Shown to everyone: an assistant
 *  may name anyone its approver. */
export function AwaySection() {
  const emailId = useId();
  const untilId = useId();
  const [rows, setRows] = useState<Delegation[] | null>(null);
  const [email, setEmail] = useState("");
  const [until, setUntil] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await approvals.delegations());
    } catch {
      setRows([]);
    }
  }, []);
  useEffect(() => void load(), [load]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // The end of the chosen day, in this browser's time zone.
      const end = new Date(`${until}T23:59:59`);
      setRows(await approvals.addDelegation({ email: email.trim(), until: end.toISOString() }));
      setEmail("");
      setUntil("");
    } catch (err) {
      setError(
        `Couldn't hand them over. ${err instanceof ApiError ? err.message : "Check your connection, then try again."}`,
      );
    } finally {
      setBusy(false);
    }
  };

  const end = async (id: string) => {
    setError(null);
    try {
      await approvals.endDelegation(id);
      await load();
    } catch (err) {
      setError(
        `Couldn't end it. ${err instanceof ApiError ? err.message : "Check your connection, then try again."}`,
      );
    }
  };

  return (
    <section id="away" aria-labelledby="away-heading" className="flex scroll-mt-20 flex-col gap-3">
      <SectionHeading
        level={2}
        id="away-heading"
        title="While you're away"
        description="If an assistant names you as an approver, someone else can decide its requests until a date you choose. You can end it early."
      />
      {rows && rows.length > 0 && (
        <ul className="border-border border-t">
          {rows.map((r) => (
            <li
              key={r.id}
              className="border-border flex flex-wrap items-center gap-x-4 gap-y-2 border-b py-3"
            >
              <p className="min-w-0 flex-1">
                <span className="font-medium">{r.delegate_name || r.delegate_email}</span> decides
                for you until {formatDate(r.until)}
              </p>
              <Button size="sm" variant="outline" onClick={() => void end(r.id)}>
                End now
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={add} className="flex flex-wrap items-end gap-3">
        <div className="flex min-w-[16rem] flex-1 flex-col gap-1">
          <Label htmlFor={emailId}>Their email</Label>
          <Input
            id={emailId}
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={untilId}>Until</Label>
          <Input
            id={untilId}
            type="date"
            required
            value={until}
            onChange={(e) => setUntil(e.target.value)}
          />
        </div>
        <Button type="submit" disabled={busy}>
          {busy ? "Handing over…" : "Hand over"}
        </Button>
      </form>
      {error && <Alert>{error}</Alert>}
    </section>
  );
}
