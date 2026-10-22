"use client";

import { useCallback, useEffect, useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SectionHeading } from "@/components/ui/section-heading";
import { Select } from "@/components/ui/select";
import { ApiError, orgs, type Erasure, type LegalHoldItem, type Member } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import {
  DAYS_MAX,
  DAYS_MIN,
  erasureDetailWords,
  erasureWords,
  retentionWords,
} from "@/lib/retention";

const message = (err: unknown, fallback: string) =>
  err instanceof ApiError ? err.message : fallback;

/** Keeping and erasing (Phase 8.10): how long conversations are kept
 *  (admins), legal holds and erasing one person (the owner), and what was
 *  erased. Removing a conversation from someone's history only hides it;
 *  this is where things go for good. */
export function DataSettings({
  orgId,
  owner,
  retentionDays,
}: {
  orgId: string;
  owner: boolean;
  retentionDays: number;
}) {
  const id = useId();
  const confirm = useConfirm();
  const [days, setDays] = useState(String(retentionDays));
  const [savedDays, setSavedDays] = useState(retentionDays);
  const [people, setPeople] = useState<Member[]>([]);
  const [holds, setHolds] = useState<LegalHoldItem[]>([]);
  const [erased, setErased] = useState<Erasure[]>([]);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [holdWho, setHoldWho] = useState("");
  const [holdConversation, setHoldConversation] = useState("");
  const [holdReason, setHoldReason] = useState("");
  const [eraseWho, setEraseWho] = useState("");
  const [eraseEmail, setEraseEmail] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    orgs
      .members(orgId)
      .then((p) => setPeople(p.items))
      .catch(() => setPeople([]));
    orgs
      .holds(orgId)
      .then(setHolds)
      .catch(() => setHolds([]));
    orgs
      .erasures(orgId)
      .then(setErased)
      .catch(() => setErased([]));
  }, [orgId]);

  useEffect(() => load(), [load]);

  const n = Number(days);
  const valid = Number.isInteger(n) && n >= DAYS_MIN && n <= DAYS_MAX;
  const words = valid ? retentionWords(n) : null;
  const target = people.find((p) => p.user_id === eraseWho);

  const act = async (what: () => Promise<string>, failure: string) => {
    setBusy(true);
    setNote(null);
    try {
      setNote({ ok: true, text: await what() });
      load();
    } catch (err) {
      setNote({ ok: false, text: `${failure} ${message(err, "Try again.")}` });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby={`${id}-heading`} className="flex max-w-160 flex-col gap-6">
      <SectionHeading
        level={2}
        id={`${id}-heading`}
        title="Keeping and erasing"
        description="Removing a conversation from someone's history only hides it. Here is how long conversations are kept before they are erased for good, legal holds, and erasing one person's data."
      />

      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-days`}>Keep conversations for (days)</Label>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            id={`${id}-days`}
            type="number"
            min={DAYS_MIN}
            max={DAYS_MAX}
            className="w-32"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
          <Button
            disabled={busy || !valid || n === savedDays}
            onClick={() =>
              void act(async () => {
                const out = await orgs.setRetention(orgId, n);
                setSavedDays(out.days);
                return `Saved: ${out.days} days.`;
              }, "Couldn't save it.")
            }
          >
            Save
          </Button>
        </div>
        <p className="text-small text-muted-foreground">
          {words ? words.meaning : `Between ${DAYS_MIN} and ${DAYS_MAX} days.`} Legal holds are
          skipped. Each night&apos;s erasing is recorded below.
        </p>
        {words?.warning && <Alert tone="warning">{words.warning}</Alert>}
      </div>

      <div className="flex flex-col gap-3">
        <h3 className="text-h4 font-semibold">Legal holds</h3>
        <p className="text-small text-muted-foreground">
          A person&apos;s conversations, or one conversation, kept out of retention and erasure
          until the hold is lifted: for a dispute or an investigation. The owner sets and lifts
          them.
        </p>
        {holds.length === 0 ? (
          <p className="text-muted-foreground">No holds.</p>
        ) : (
          <ul className="border-border flex flex-col border-t">
            {holds.map((h) => (
              <li
                key={h.id}
                className="border-border flex flex-wrap items-start gap-2 border-b py-2"
              >
                <div className="min-w-0 flex-1">
                  <p className="font-medium">
                    {h.user_email ?? (h.conversation_id ? `Conversation ${h.conversation_id}` : "")}
                  </p>
                  <p className="text-small text-muted-foreground">
                    {h.reason}. Since {formatRelative(h.created_at)}
                    {h.released_at ? `; lifted ${formatRelative(h.released_at)}` : ""}
                  </p>
                </div>
                {owner && !h.released_at && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      void act(async () => {
                        await orgs.releaseHold(orgId, h.id);
                        return "Hold lifted.";
                      }, "Couldn't lift it.")
                    }
                  >
                    Lift
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {owner && (
          <form
            className="flex flex-col gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                await orgs.createHold(orgId, {
                  user_id: holdWho || null,
                  conversation_id: holdWho ? null : holdConversation.trim() || null,
                  reason: holdReason,
                });
                setHoldReason("");
                setHoldConversation("");
                return "Hold set.";
              }, "Couldn't set the hold.");
            }}
          >
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`${id}-hold-who`}>A person</Label>
                <Select
                  id={`${id}-hold-who`}
                  value={holdWho}
                  onChange={(e) => setHoldWho(e.target.value)}
                >
                  <option value="">Or one conversation instead</option>
                  {people.map((p) => (
                    <option key={p.user_id} value={p.user_id}>
                      {p.name ? `${p.name} (${p.email})` : p.email}
                    </option>
                  ))}
                </Select>
              </div>
              {!holdWho && (
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={`${id}-hold-conv`}>Conversation id</Label>
                  <Input
                    id={`${id}-hold-conv`}
                    value={holdConversation}
                    onChange={(e) => setHoldConversation(e.target.value)}
                    placeholder="From the conversation's address"
                  />
                </div>
              )}
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${id}-hold-reason`}>Reason</Label>
              <Input
                id={`${id}-hold-reason`}
                maxLength={500}
                value={holdReason}
                onChange={(e) => setHoldReason(e.target.value)}
                placeholder="e.g. POSH inquiry 2026-14"
              />
            </div>
            <Button
              type="submit"
              variant="outline"
              className="self-start"
              disabled={
                busy || holdReason.trim().length < 3 || (!holdWho && !holdConversation.trim())
              }
            >
              Set a hold
            </Button>
          </form>
        )}
      </div>

      {owner && (
        <div className="flex flex-col gap-3">
          <h3 className="text-h4 font-semibold">Erase one person&apos;s data</h3>
          <p className="text-small text-muted-foreground">
            Their conversations (with their run traces, summaries and share links), what the
            assistants remember about them, and their feedback comments, erased for good. Their
            account and the audit log stay, and their spend stays in the totals without their name.
            Not while they are under a hold.
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${id}-erase-who`}>Person</Label>
              <Select
                id={`${id}-erase-who`}
                value={eraseWho}
                onChange={(e) => {
                  setEraseWho(e.target.value);
                  setEraseEmail("");
                }}
              >
                <option value="">Choose someone</option>
                {people.map((p) => (
                  <option key={p.user_id} value={p.user_id}>
                    {p.name ? `${p.name} (${p.email})` : p.email}
                  </option>
                ))}
              </Select>
            </div>
            {target && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`${id}-erase-email`}>Type their email to confirm</Label>
                <Input
                  id={`${id}-erase-email`}
                  value={eraseEmail}
                  onChange={(e) => setEraseEmail(e.target.value)}
                  placeholder={target.email}
                  autoComplete="off"
                />
              </div>
            )}
          </div>
          <Button
            variant="destructive"
            className="self-start"
            disabled={
              busy || !target || eraseEmail.trim().toLowerCase() !== target.email.toLowerCase()
            }
            onClick={async () => {
              if (!target) return;
              const sure = await confirm({
                title: `Erase ${target.email}'s data for good?`,
                description:
                  "Their conversations, memory notes and feedback comments are deleted and can't be brought back, except by restoring an older backup (which erases them again).",
                confirmLabel: "Erase for good",
                destructive: true,
              });
              if (!sure) return;
              void act(async () => {
                const out = await orgs.erasePerson(orgId, target.user_id, eraseEmail.trim());
                setEraseWho("");
                setEraseEmail("");
                return `Erased: ${out.conversations} conversations, ${out.memories} memory notes, ${out.comments} comments${out.kept ? `; ${out.kept} kept (held or answering)` : ""}.`;
              }, "Couldn't erase it.");
            }}
          >
            Erase for good
          </Button>
        </div>
      )}

      {note && <Alert tone={note.ok ? "success" : undefined}>{note.text}</Alert>}

      <div className="flex flex-col gap-2">
        <h3 className="text-h4 font-semibold">What was erased</h3>
        {erased.length === 0 ? (
          <p className="text-muted-foreground">Nothing yet.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {erased.map((e, i) => (
              <li key={`${e.created_at}-${i}`} className="text-small">
                {erasureWords(e)},{" "}
                <span className="text-muted-foreground">{formatRelative(e.created_at)}</span>
                {/* Phase 10.5: what went with the conversations. */}
                {erasureDetailWords((e as { details?: Record<string, number> }).details) && (
                  <span className="text-muted-foreground block">
                    {erasureDetailWords((e as { details?: Record<string, number> }).details)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
