"use client";

import { LifeBuoy } from "lucide-react";
import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, handoffs, type Handoff, type HandoffList } from "@/lib/api";
import { formatRelative } from "@/lib/format";

/** "Get help from a person" (Phase 9.5). Shown only when the assistant
 *  hands off: its contact is emailed a link to this conversation (never
 *  what was said), or a ticket is opened, as its builder set it up. */
export function HandoffButton({ conversationId }: { conversationId: string }) {
  const [info, setInfo] = useState<HandoffList | null>(null);
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Handoff | null>(null);

  useEffect(() => {
    let live = true;
    handoffs
      .list(conversationId)
      .then((r) => live && setInfo(r))
      // Nothing to offer when it can't be read: the button stays away.
      .catch(() => live && setInfo(null));
    return () => {
      live = false;
    };
  }, [conversationId]);

  if (!info?.available) return null;
  const contact = info.contact_email;
  const last = info.items[0];

  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      const row = await handoffs.create(conversationId, note.trim());
      setDone(row);
      setInfo({ ...info, items: [row, ...info.items] });
      if (row.status === "sent") setNote("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't go through. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const outcome = (row: Handoff) => {
    if (row.status === "failed") {
      return row.channel === "action"
        ? `The ticket couldn't be opened${row.ticket_ref ? ` (${row.ticket_ref})` : ""}. Try again in a moment, or email ${contact}.`
        : `The email to ${contact} couldn't be sent. Try again in a moment, or email them yourself.`;
    }
    return row.channel === "action"
      ? `A ticket was opened with a summary of this conversation${row.ticket_ref ? `: ${row.ticket_ref}` : "."}`
      : `Sent to ${contact}. They'll open this conversation in the app.`;
  };

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => {
          setOpen(true);
          setDone(null);
          setError(null);
        }}
      >
        <LifeBuoy aria-hidden className="size-4" />
        Get help from a person
      </Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        dismissOnBackdrop={false}
        title="Get help from a person"
        description={
          info.opens_ticket
            ? "This opens a ticket with a short summary of this conversation, so someone on the team can pick it up."
            : `${contact} gets an email with a link to this conversation. What you said isn't in the email: they read it here, in the app.`
        }
        footer={
          done ? (
            <Button onClick={() => setOpen(false)}>Done</Button>
          ) : (
            <>
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button disabled={busy} onClick={() => void send()}>
                {busy ? "Sending…" : info.opens_ticket ? "Open a ticket" : `Send to ${contact}`}
              </Button>
            </>
          )
        }
      >
        <div className="flex flex-col gap-4">
          {done ? (
            <Alert tone={done.status === "sent" ? "success" : "destructive"}>{outcome(done)}</Alert>
          ) : (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`handoff-note-${conversationId}`}>What do you need? (optional)</Label>
              <Textarea
                id={`handoff-note-${conversationId}`}
                value={note}
                maxLength={1000}
                onChange={(e) => setNote(e.target.value)}
                placeholder="For example: I still can't sign in to the VPN after resetting my password."
              />
              <p className="text-small text-muted-foreground">
                Don&apos;t include passwords: secrets and links are taken out.
              </p>
            </div>
          )}
          {error && <Alert>{error}</Alert>}
          {last && !done && (
            <p className="text-small text-muted-foreground">
              Last asked {formatRelative(last.created_at)}:{" "}
              {last.status === "sent" ? "it went through." : "it didn't go through."}
            </p>
          )}
        </div>
      </Dialog>
    </>
  );
}
