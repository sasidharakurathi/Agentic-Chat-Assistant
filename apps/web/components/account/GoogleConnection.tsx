"use client";

import { useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { SectionHeading } from "@/components/ui/section-heading";
import { ApiError, auth } from "@/lib/api";
import { googleError, googleStartUrl } from "@/lib/google";

/** Google on Your account (Phase 8.3). Connecting is done here, signed in:
 *  that is what proves the account is yours, so a Google sign-in never
 *  joins an account by its address. */
export function GoogleConnection({
  connected,
  hasPassword,
  available,
  outcome,
  onChange,
}: {
  /** The Google account's address, when connected. */
  connected: string | null;
  hasPassword: boolean;
  /** The server has Google sign-in set up. */
  available: boolean;
  /** `?google=` on the way back from Google. */
  outcome: string | null;
  onChange: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(
    outcome === "linked"
      ? { ok: true, text: "Google is connected: you can sign in with it now." }
      : outcome
        ? { ok: false, text: googleError(outcome) ?? "" }
        : null,
  );

  if (!available && !connected) return null;

  const connect = async () => {
    setBusy(true);
    setNote(null);
    try {
      const { ticket } = await auth.googleLinkTicket();
      window.location.assign(googleStartUrl({ ticket }));
    } catch (err) {
      setNote({
        ok: false,
        text: err instanceof ApiError ? err.message : "Couldn't reach the server. Try again.",
      });
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    setNote(null);
    try {
      setNote({ ok: true, text: (await auth.googleDisconnect()).message });
      onChange();
    } catch (err) {
      setNote({
        ok: false,
        text: err instanceof ApiError ? err.message : "Couldn't reach the server. Try again.",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="google-heading" className="flex flex-col gap-3">
      <SectionHeading
        level={2}
        id="google-heading"
        title="Google"
        description={
          connected
            ? `Connected: ${connected}. You can sign in with Google.`
            : "Connect your company Google account to sign in with it."
        }
      />
      {connected ? (
        hasPassword ? (
          <Button
            variant="outline"
            className="self-start"
            disabled={busy}
            onClick={() => void disconnect()}
          >
            {busy ? "Disconnecting…" : "Disconnect Google"}
          </Button>
        ) : (
          <p className="text-small text-muted-foreground">
            Google is how this account signs in, so it stays connected.
          </p>
        )
      ) : (
        <Button className="self-start" disabled={busy} onClick={() => void connect()}>
          {busy ? "Opening Google…" : "Connect Google"}
        </Button>
      )}
      {note && note.text && <Alert tone={note.ok ? "success" : undefined}>{note.text}</Alert>}
    </section>
  );
}
