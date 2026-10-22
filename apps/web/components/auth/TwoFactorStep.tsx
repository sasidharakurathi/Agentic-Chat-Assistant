"use client";

import { useId, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

/** A sign-in's second step (Phase 8.4): the code from the authenticator
 *  app, or one of the recovery codes. */
export function TwoFactorStep({
  challenge,
  next,
  onRestart,
}: {
  challenge: string;
  next?: string | null;
  /** The challenge expired: back to the password. */
  onRestart: () => void;
}) {
  const { finishTwoFactor } = useAuth();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const id = useId();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      await finishTwoFactor(challenge, code, next);
    } catch (err) {
      setPending(false);
      if (err instanceof ApiError && err.code === "challenge_expired") {
        onRestart();
        return;
      }
      setError(
        err instanceof ApiError
          ? err.status === 429
            ? "Too many tries. Wait a few minutes, then try again."
            : "That code isn't right. Use the code the app shows now, or a recovery code."
          : "Couldn't reach the server. Check your connection, then try again.",
      );
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={id}>Code from your authenticator app</Label>
        <Input
          id={id}
          inputMode="numeric"
          autoComplete="one-time-code"
          autoFocus
          required
          value={code}
          onChange={(e) => setCode(e.target.value)}
          aria-describedby={`${id}-hint`}
        />
        <p id={`${id}-hint`} className="text-small text-muted-foreground">
          Lost your phone? Type one of your recovery codes instead.
        </p>
      </div>
      {error && <Alert>{error}</Alert>}
      <Button type="submit" disabled={pending}>
        {pending ? "Checking…" : "Sign in"}
      </Button>
    </form>
  );
}
