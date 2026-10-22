"use client";

import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { SectionHeading } from "@/components/ui/section-heading";
import { ApiError, orgs } from "@/lib/api";
import type { Schemas } from "@assistant-studio/shared";

type Status = Schemas["MailStatusOut"];

const TRANSPORT: Record<Status["transport"], string> = {
  smtp: "Sends email",
  console: "Writes email to the server's log (development)",
  memory: "Keeps email in memory (tests)",
  off: "Sends no email",
};

/** How this server sends email (Phase 7b.5), for admins. Read-only: the
 *  settings live in the server's environment, so nobody can point the
 *  server at another mail server from here. The test goes to you only. */
export function MailStatus({ orgId }: { orgId: string }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    orgs
      .mailStatus(orgId)
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [orgId]);

  if (!status) return null;

  const test = async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await orgs.mailTest(orgId);
      setNote({
        ok: r.queued,
        text: r.queued
          ? `A test email is on its way to ${r.to}.`
          : "Email is off on this server, so nothing was sent.",
      });
    } catch (err) {
      setNote({
        ok: false,
        text: `Couldn't send a test. ${err instanceof ApiError ? err.message : "Try again."}`,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="mail-heading" className="flex max-w-160 flex-col gap-3">
      <SectionHeading
        level={2}
        id="mail-heading"
        title="Email"
        description="Invites, versions waiting for review, and reviews done. Set on the server, not here."
      />
      <div className="border-border flex flex-wrap items-center gap-x-4 gap-y-2 rounded-md border px-3 py-2">
        <div className="text-small min-w-0 flex-1">
          <p className="text-foreground">{TRANSPORT[status.transport]}</p>
          {status.from_address && (
            <p className="text-muted-foreground">
              From {status.from_address}
              {status.host ? ` through ${status.host}` : ""}
            </p>
          )}
          {status.transport === "smtp" && (
            <p className="text-muted-foreground">
              Last 7 days: {status.sent_7d} sent, {status.failed_7d} failed
            </p>
          )}
        </div>
        {status.transport !== "off" && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void test()}
          >
            Send me a test
          </Button>
        )}
      </div>
      {status.transport === "off" && (
        <p className="text-small text-muted-foreground">
          Copy each invite link and send it yourself. To have it emailed, set SMTP_HOST and
          MAIL_FROM on the server (OPERATIONS.md).
        </p>
      )}
      {note && <Alert tone={note.ok ? "success" : undefined}>{note.text}</Alert>}
    </section>
  );
}
