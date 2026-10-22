"use client";

import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { SectionHeading } from "@/components/ui/section-heading";
import { ApiError, auth as authApi } from "@/lib/api";
import { showCode } from "@/lib/standalone";

/** Making a code to sign in on another device (Phase 8.6): the way into
 *  the iPhone home-screen app when Google sign-in ends up in Safari, or
 *  onto any device without typing a password. Five minutes, once; a new
 *  one voids the last. */
export function DeviceCodeSection() {
  const [made, setMade] = useState<{ code: string; until: number } | null>(null);
  const [left, setLeft] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!made) return;
    const tick = () => setLeft(Math.max(0, Math.round((made.until - Date.now()) / 1000)));
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [made]);

  const make = async () => {
    setBusy(true);
    setError(null);
    try {
      const out = await authApi.deviceCode();
      setMade({ code: out.code, until: new Date(out.expires_at).getTime() });
    } catch (err) {
      setError(
        `Couldn't make a code. ${err instanceof ApiError ? err.message : "Check your connection, then try again."}`,
      );
    } finally {
      setBusy(false);
    }
  };

  const live = made !== null && left > 0;
  return (
    <section aria-labelledby="device-code-heading" className="flex flex-col gap-3">
      <SectionHeading
        level={2}
        id="device-code-heading"
        title="Sign in on another device"
        description="For the phone app, or any device where signing in doesn't stick: make a code here, then type it into that device's sign-in page."
      />
      {live ? (
        <div className="border-border flex flex-col gap-2 rounded-lg border p-4">
          <p
            className="text-h2 font-mono font-semibold tracking-widest"
            aria-label={`Code ${made.code.split("").join(" ")}`}
          >
            {showCode(made.code)}
          </p>
          <p className="text-small text-muted-foreground" role="status">
            Works once, for {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")} more.
          </p>
          <p className="text-small">
            Never give this code to anyone, IT included: whoever has it signs in as you.
          </p>
        </div>
      ) : (
        made && <p className="text-small text-muted-foreground">That code has run out.</p>
      )}
      {error && <Alert>{error}</Alert>}
      <Button variant="outline" className="self-start" disabled={busy} onClick={() => void make()}>
        {busy ? "Making a code…" : live ? "Make a new code" : "Make a code"}
      </Button>
    </section>
  );
}
