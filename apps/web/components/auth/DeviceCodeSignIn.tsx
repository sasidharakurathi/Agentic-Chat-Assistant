"use client";

import { useEffect, useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, auth as authApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { isInstalledApp } from "@/lib/standalone";

/** Signing in with a code from another device (Phase 8.6): for the iPhone
 *  home-screen app, whose storage Safari doesn't share, so a Google sign-in
 *  that ends in Safari leaves the app signed out. Sign in anywhere, make a
 *  code in Your account, type it here. Open from the start in the
 *  installed app. `onChallenge`: the account has two-step sign-in, and its
 *  code finishes it, as after a password. */
export function DeviceCodeSignIn({
  next,
  onChallenge,
}: {
  next: string | null;
  onChallenge: (challenge: string) => void;
}) {
  const { adopt } = useAuth();
  const [open, setOpen] = useState(false);
  const [installed, setInstalled] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();

  // After mounting: the server render can't know.
  useEffect(() => {
    const app = isInstalledApp();
    setInstalled(app);
    if (app) setOpen(true);
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const challenge = await adopt(await authApi.redeemDeviceCode(code), next);
      if (challenge) onChallenge(challenge);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Couldn't reach the server. Are you on the office network or the company VPN?",
      );
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <p className="text-muted-foreground text-sm">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="text-foreground focus-visible:ring-ring rounded-sm font-medium underline decoration-1 underline-offset-[3px] hover:decoration-2 focus-visible:ring-2 focus-visible:outline-none"
        >
          Sign in with a code from another device
        </button>
      </p>
    );
  }
  return (
    <form
      onSubmit={submit}
      aria-labelledby={`${id}-title`}
      className="border-border flex flex-col gap-3 rounded-lg border p-4"
    >
      <p id={`${id}-title`} className="font-medium">
        Sign in with a code
      </p>
      <p className="text-small text-muted-foreground">
        {installed
          ? "In the home-screen app, sign in with your password, or with a code: sign in on Safari or a computer, open Your account, and make a code there."
          : "On a device where you're signed in, open Your account and make a code."}
      </p>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-code`}>Code</Label>
        <Input
          id={`${id}-code`}
          required
          autoComplete="one-time-code"
          autoCapitalize="characters"
          spellCheck={false}
          maxLength={20}
          placeholder="ABCD-EFGH"
          className="font-mono tracking-wider"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </div>
      {error && <Alert>{error}</Alert>}
      <Button type="submit" disabled={busy || !code.trim()}>
        {busy ? "Signing in…" : "Sign in with the code"}
      </Button>
    </form>
  );
}
