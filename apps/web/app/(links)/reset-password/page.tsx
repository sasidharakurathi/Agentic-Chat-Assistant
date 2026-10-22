"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AuthCard } from "@/app/(auth)/auth-card";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, auth } from "@/lib/api";
import { tokenFromHash } from "@/lib/link-token";

const LINK =
  "text-foreground rounded-sm font-medium underline decoration-1 underline-offset-[3px] hover:decoration-2";

/** Choosing a new password from an emailed link (Phase 7b.6). Opening the
 *  page changes nothing: the password changes only when the form is sent,
 *  so a mail scanner following the link does no harm. */
export default function ResetPasswordPage() {
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    setToken(tokenFromHash(window.location.hash));
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    if (password !== again) {
      setError("The two passwords don't match.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await auth.confirmReset(token, password);
      setDone(true);
      window.history.replaceState(null, "", "/reset-password");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Couldn't reach the server. Check your connection, then try again.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthCard>
      <CardHeader>
        <h1 className="font-condensed text-h1 font-semibold">Choose a new password</h1>
        <CardDescription>Every device signed in to your account is signed out.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        {token === null && (
          <Alert role="alert">
            This link is incomplete. Open it again from the email, or{" "}
            <Link href="/forgot-password" className={LINK}>
              ask for a new one
            </Link>
            .
          </Alert>
        )}
        {done ? (
          <>
            <Alert tone="success">Your password is changed.</Alert>
            <Link href="/login" className={LINK}>
              Sign in with it
            </Link>
          </>
        ) : (
          token && (
            <form onSubmit={onSubmit} className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="password">New password</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="new-password"
                  minLength={8}
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <p className="text-small text-muted-foreground">At least 8 characters.</p>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="again">The same again</Label>
                <Input
                  id="again"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={again}
                  onChange={(e) => setAgain(e.target.value)}
                />
              </div>
              {error && (
                <Alert role="alert">
                  {error}{" "}
                  {error.includes("expired") && (
                    <Link href="/forgot-password" className={LINK}>
                      Ask for a new link
                    </Link>
                  )}
                </Alert>
              )}
              <Button type="submit" disabled={pending}>
                {pending ? "Saving…" : "Set my new password"}
              </Button>
            </form>
          )
        )}
      </CardContent>
    </AuthCard>
  );
}
